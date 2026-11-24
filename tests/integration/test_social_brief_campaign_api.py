# -*- coding: utf-8 -*-
"""Kampanya brief'i oluşturma + okuma (plan_sosyal_kampanya_akisi.md Faz 1)."""
from __future__ import annotations

import pytest

from app.config import settings
from app.database.models import SocialBrief
from tests.integration.test_social_brief_create_api_f1d5 import (  # noqa: F401
    _setup_fresh_environment,
    _valid_payload,
    bind_dependencies_get_db,
    enable_flag,
)

URL = "/api/v1/generation/social/briefs"
CAMPAIGN = {
    "mode": "suggest",
    "usage": "paid",
    "objective": "sales",
    "destination": "whatsapp",
    "promoted_item": "Başlangıç paketi",
    "disallowed_offer_types": ["percent_discount"],
}


@pytest.fixture
def enable_campaign(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_CAMPAIGN_BRIEF", True)


@pytest.fixture
def env(db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    return ws, _valid_payload(run.id, [kws[0].id, kws[1].id])


def test_campaign_rejected_when_flag_off(client, db_session, env, enable_flag):
    ws, payload = env
    resp = client.post(URL, params={"brand_profile_id": ws.id}, json={**payload, "campaign": CAMPAIGN})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "CAMPAIGN_DISABLED"
    assert db_session.query(SocialBrief).count() == 0


def test_organic_brief_unchanged_when_campaign_flag_on(client, env, enable_flag, enable_campaign):
    ws, payload = env
    resp = client.post(URL, params={"brand_profile_id": ws.id}, json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["campaign_spec"] is None


def test_campaign_brief_persisted_and_readable(client, db_session, env, enable_flag, enable_campaign):
    ws, payload = env
    resp = client.post(URL, params={"brand_profile_id": ws.id}, json={**payload, "campaign": CAMPAIGN})
    assert resp.status_code == 201, resp.text
    spec = resp.json()["campaign_spec"]
    assert spec["mode"] == "suggest"
    assert spec["disallowed_offer_types"] == ["percent_discount"]
    assert spec["offer_draft"] is None

    brief_id = resp.json()["id"]
    db_session.expire_all()
    assert db_session.get(SocialBrief, brief_id).campaign_spec == spec

    read = client.get(f"{URL}/{brief_id}", params={"brand_profile_id": ws.id})
    assert read.status_code == 200, read.text
    assert read.json()["campaign_spec"] == spec


def test_invalid_campaign_returns_422_and_persists_nothing(
    client, db_session, env, enable_flag, enable_campaign
):
    ws, payload = env
    bad = {**CAMPAIGN, "mode": "defined", "disallowed_offer_types": []}  # offer_draft eksik
    resp = client.post(URL, params={"brand_profile_id": ws.id}, json={**payload, "campaign": bad})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "CAMPAIGN_FIELD_REQUIRED"
    assert resp.json()["detail"]["field"] == "campaign.offer_draft"
    assert db_session.query(SocialBrief).count() == 0


# ---------- Faz 4: arayüzün okuduğu uçlar ----------

def test_format_matrix_reports_campaign_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_CAMPAIGN_BRIEF", False)
    assert client.get("/api/v1/generation/social/format-matrix").json()["campaign_enabled"] is False
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_CAMPAIGN_BRIEF", True)
    assert client.get("/api/v1/generation/social/format-matrix").json()["campaign_enabled"] is True
