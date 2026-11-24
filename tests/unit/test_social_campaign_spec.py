# -*- coding: utf-8 -*-
"""Kampanya brief girdisi saf doğrulaması (plan_sosyal_kampanya_akisi.md §1-§2, Faz 1)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.social import campaign_spec

from app.core.social.brief_validation import (
    SocialBriefValidationError,
    validate_social_brief_request,
)
from app.core.social.campaign_spec import (
    SPEC_VERSION,
    CampaignSpecError,
    validate_campaign_spec,
)
from app.schemas.social_brief import (
    SocialBriefCampaignRequest,
    SocialBriefCreateRequest,
    SocialBriefTargetCreateRequest,
)

TODAY = date(2026, 11, 17)


def _offer(**over):
    base = dict(type="percent_discount", value=" %20 ", applies_to="Başlangıç paketi",
                conditions=None, ends=None)
    base.update(over)
    return SimpleNamespace(**base)


def _campaign(**over):
    base = dict(mode="suggest", usage="paid", objective="sales", destination="whatsapp",
                promoted_item="Başlangıç paketi", audience=None, focus_note=None,
                disallowed_offer_types=[], offer_draft=None)
    base.update(over)
    return SimpleNamespace(**base)


def _err(campaign) -> CampaignSpecError:
    with pytest.raises(CampaignSpecError) as exc:
        validate_campaign_spec(campaign, today=TODAY)
    return exc.value


def test_suggest_minimal_spec():
    spec = validate_campaign_spec(_campaign(disallowed_offer_types=["percent_discount"]), today=TODAY)
    assert spec == {
        "spec_version": SPEC_VERSION, "mode": "suggest", "usage": "paid", "objective": "sales",
        "destination": "whatsapp", "promoted_item": "Başlangıç paketi", "audience": None,
        "focus_note": None, "included_features": None, "disallowed_offer_types": ["percent_discount"],
        "offer_draft": None,
    }
    spec = validate_campaign_spec(_campaign(included_features="  Sınırsız portföy takibi "), today=TODAY)
    assert spec["included_features"] == "Sınırsız portföy takibi"


def test_defined_spec_trims_offer_and_keeps_future_end_date():
    spec = validate_campaign_spec(
        _campaign(mode="defined", offer_draft=_offer(conditions="kod: YAZ20", ends="2026-11-30")),
        today=TODAY,
    )
    assert spec["offer_draft"] == {
        "type": "percent_discount", "value": "%20", "applies_to": "Başlangıç paketi",
        "conditions": "kod: YAZ20", "ends": "2026-11-30",
    }


def test_end_date_today_is_accepted():
    spec = validate_campaign_spec(
        _campaign(mode="defined", offer_draft=_offer(ends="2026-11-17")), today=TODAY)
    assert spec["offer_draft"]["ends"] == "2026-11-17"


@pytest.mark.parametrize("campaign, code, field", [
    (_campaign(mode="auto"), "CAMPAIGN_INVALID_VALUE", "campaign.mode"),
    (_campaign(usage="both"), "CAMPAIGN_INVALID_VALUE", "campaign.usage"),
    (_campaign(objective="Sales"), "CAMPAIGN_INVALID_VALUE", "campaign.objective"),
    (_campaign(destination="email"), "CAMPAIGN_INVALID_VALUE", "campaign.destination"),
    (_campaign(promoted_item="   "), "CAMPAIGN_FIELD_REQUIRED", "campaign.promoted_item"),
    (_campaign(promoted_item="x" * 201), "CAMPAIGN_TEXT_TOO_LONG", "campaign.promoted_item"),
    (_campaign(focus_note="x" * 301), "CAMPAIGN_TEXT_TOO_LONG", "campaign.focus_note"),
    (_campaign(included_features="x" * 501), "CAMPAIGN_TEXT_TOO_LONG", "campaign.included_features"),
    # "none" brief'te seçilemez; yalnız içerik isteğinde (plan §2)
    (_campaign(disallowed_offer_types=["none"]), "CAMPAIGN_INVALID_VALUE",
     "campaign.disallowed_offer_types"),
    (_campaign(disallowed_offer_types=["gift", "gift"]), "CAMPAIGN_INVALID_VALUE",
     "campaign.disallowed_offer_types"),
    (_campaign(disallowed_offer_types=["percent_discount", "bundle", "free_shipping", "gift"]),
     "CAMPAIGN_INVALID_VALUE", "campaign.disallowed_offer_types"),
    (_campaign(offer_draft=_offer()), "CAMPAIGN_MODE_MISMATCH", "campaign.offer_draft"),
    (_campaign(mode="defined"), "CAMPAIGN_FIELD_REQUIRED", "campaign.offer_draft"),
    (_campaign(mode="defined", offer_draft=_offer(), disallowed_offer_types=["gift"]),
     "CAMPAIGN_MODE_MISMATCH", "campaign.disallowed_offer_types"),
    (_campaign(mode="defined", offer_draft=_offer(type="none")), "CAMPAIGN_INVALID_VALUE",
     "campaign.offer_draft.type"),
    (_campaign(mode="defined", offer_draft=_offer(value=" ")), "CAMPAIGN_FIELD_REQUIRED",
     "campaign.offer_draft.value"),
    (_campaign(mode="defined", offer_draft=_offer(applies_to=None)), "CAMPAIGN_FIELD_REQUIRED",
     "campaign.offer_draft.applies_to"),
    (_campaign(mode="defined", offer_draft=_offer(ends="30.11.2026")), "CAMPAIGN_INVALID_VALUE",
     "campaign.offer_draft.ends"),
    (_campaign(mode="defined", offer_draft=_offer(ends="2026-11-16")), "CAMPAIGN_OFFER_EXPIRED",
     "campaign.offer_draft.ends"),
])
def test_rejections(campaign, code, field):
    err = _err(campaign)
    assert (err.error_code, err.field) == (code, field)


def test_business_day_is_istanbul_not_server_utc(monkeypatch):
    # UTC 18 Kasım 21:00 = Türkiye 19 Kasım 00:00 -> gün dönmüş olmalı
    assert campaign_spec.business_today(datetime(2026, 11, 18, 20, 59, tzinfo=timezone.utc)) == date(2026, 11, 18)
    assert campaign_spec.business_today(datetime(2026, 11, 18, 21, 0, tzinfo=timezone.utc)) == date(2026, 11, 19)

    # Varsayılan yol (today verilmeden) iş gününü kullanır: TR'de 19'u iken 18'inde biten teklif reddedilir
    monkeypatch.setattr(campaign_spec, "business_today", lambda: date(2026, 11, 19))
    with pytest.raises(CampaignSpecError) as exc:
        validate_campaign_spec(_campaign(mode="defined", offer_draft=_offer(ends="2026-11-18")))
    assert exc.value.error_code == "CAMPAIGN_OFFER_EXPIRED"


def _request(campaign=None) -> SocialBriefCreateRequest:
    return SocialBriefCreateRequest(
        scoring_run_id=1, keyword_ids=[10],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
        campaign=campaign,
    )


def test_organic_request_has_no_campaign_spec():
    assert validate_social_brief_request(_request()).campaign_spec is None


def test_brief_request_carries_campaign_spec_and_maps_errors():
    campaign = SocialBriefCampaignRequest(
        mode="suggest", usage="organic", objective="lead", destination="form",
        promoted_item="Danışmanlık",
    )
    spec = validate_social_brief_request(_request(campaign)).campaign_spec
    assert spec["mode"] == "suggest" and spec["usage"] == "organic"

    bad = campaign.model_copy(update={"usage": "both"})
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(_request(bad))
    assert (exc.value.error_code, exc.value.field) == ("CAMPAIGN_INVALID_VALUE", "campaign.usage")
