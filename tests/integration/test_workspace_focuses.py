"""Marka altinda alt sayfa odaklari (plan_marka_alt_sayfa.md Faz 3a + 3b).

Gercek test DB'si; AI ve crawler sahte (ag/ucretli cagri yok). Dispatch edilen
arka plan gorevleri kaydedilir; gorev govdeleri ayri testlerde dogrudan cagrilir.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm.attributes import flag_modified

import app.core.site_analyzer.profile_extractor as extractor_module
import app.generators.ai_service as ai_service_module
from app.api.v1 import brand_profile as brand_profile_api
from app.core.site_analyzer.crawler import CrawlContentUnusable
from app.core.site_analyzer.profile_extractor import (
    PAGE_SCOPE_RULES,
    PROFILE_EXTRACTION_PROMPT,
    ProfileExtractor,
    build_brand_context,
    build_profile_prompt,
)
from app.database.connection import SessionLocal
from app.database.models import BrandProfile

BASE = "/api/v1/brand-profile"
# `dispatched` fixture'i modul ozniteligini ezer; gorev govdesi bu referansla cagrilir
run_profile_analysis_first = brand_profile_api._run_profile_analysis_first
PARENT_URL = "https://www.example.test"

LOCATION = {
    "location_filter_mode": "focus_only",
    "focus_cities": ["Ankara"],
    "location_exempt_terms": ["ankara bilgisayar"],
}


def _parent_profile() -> dict:
    return {
        "company_name": "Acme Danismanlik",
        "sector": "Tarim danismanligi",
        "brand_summary": "Tarim analiz hizmetleri",
        "target_audience": "Ciftciler",
        "products": ["GizliUrunX"],
        "services": ["GizliHizmetY"],
        "use_cases": ["GizliSenaryoZ"],
        "problems_solved": ["GizliSorunW"],
        "brand_terms": ["Acme"],
        "exclude_themes": ["kripto"],
        "anchor_texts": ["GizliUrunX"],
        **LOCATION,
    }


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_analysis_first", lambda **kw: calls.append(kw)
    )
    return calls


@pytest.fixture
def parent(make_workspace):
    return make_workspace(
        name="Acme",
        company_url=PARENT_URL,
        status="confirmed",
        onboarding_flow="profile_first",
        profile_data=_parent_profile(),
        competitor_terms=[{"term": "Rakip", "status": "approved", "manual_approved": True}],
        competitor_url_decisions={"rakip.test": {"url": "https://rakip.test", "decision": "block"}},
        competitor_policy={"ads": "block", "seo": "allow", "social": "block"},
        topic_policy={"excluded_terms": [{"term": "kripto"}], "excluded_aliases": []},
        capabilities={"free_plan": {"value": True, "source": "user"}},
        excluded_info="kripto",
        default_geo_target_id="2792",
        default_language_id="1037",
    )


def _post(client, workspace_id, url):
    return client.post(f"{BASE}/workspaces/{workspace_id}/focuses", json={"url": url})


# ── olusturma: kurallar ────────────────────────────────────────────


def test_create_focus(client, db_session, parent, dispatched):
    resp = _post(client, parent.id, "https://www.example.test/hizmetler/toprak-analizi")
    assert resp.status_code == 201
    body = resp.json()
    assert body["parent_brand_profile_id"] == parent.id
    assert body["archived_with_parent"] is False
    assert body["crawl_scope"] == "page"
    assert body["onboarding_flow"] == "profile_first"
    assert body["status"] == "pending"
    assert body["name"] == "Acme — /hizmetler/toprak-analizi"
    assert body["company_url"] == "https://www.example.test/hizmetler/toprak-analizi"
    assert len(dispatched) == 1
    assert dispatched[0]["workspace_id"] == body["id"]
    assert dispatched[0]["competitor_urls"] is None
    assert dispatched[0]["attempt_id"]
    row = db_session.get(BrandProfile, body["id"])
    assert row.analysis_attempt_id == dispatched[0]["attempt_id"]


def test_create_focus_without_scheme_and_listing_carries_relation(
    client, parent, dispatched
):
    resp = _post(client, parent.id, "example.test/hizmetler/a")
    assert resp.status_code == 201
    assert resp.json()["company_url"] == "https://example.test/hizmetler/a"
    listed = {w["id"]: w for w in client.get(f"{BASE}/workspaces").json()}
    assert listed[resp.json()["id"]]["parent_brand_profile_id"] == parent.id
    assert listed[resp.json()["id"]]["archived_with_parent"] is False
    assert listed[parent.id]["parent_brand_profile_id"] is None


def test_unknown_parent_404(client, dispatched):
    assert _post(client, 99999, "https://example.test/a").status_code == 404


def test_archived_parent_404(client, make_workspace, dispatched):
    archived = make_workspace(
        company_url=PARENT_URL, status="confirmed", deleted_at=datetime.now(timezone.utc)
    )
    assert _post(client, archived.id, "https://example.test/a").status_code == 404


def test_standalone_page_workspace_cannot_be_parent(client, make_workspace, dispatched):
    page = make_workspace(
        company_url="https://example.test/x", status="confirmed", crawl_scope="page"
    )
    resp = _post(client, page.id, "https://example.test/y")
    assert resp.status_code == 400
    assert "marka" in resp.json()["detail"]


def test_focus_cannot_be_parent(client, make_workspace, parent, dispatched):
    focus = make_workspace(
        company_url="https://www.example.test/a",
        status="confirmed",
        crawl_scope="page",
        parent_brand_profile_id=parent.id,
    )
    assert _post(client, focus.id, "https://www.example.test/b").status_code == 400


def test_unconfirmed_parent_409(client, make_workspace, dispatched):
    brand = make_workspace(company_url=PARENT_URL, status="profile_review")
    resp = _post(client, brand.id, "https://www.example.test/a")
    assert resp.status_code == 409
    assert "onaylayın" in resp.json()["detail"]


@pytest.mark.parametrize(
    "url", ["https://other.test/a", "https://example.test.evil.com/a", "ftp://example.test/a"]
)
def test_other_site_or_scheme_400(client, parent, dispatched, url):
    assert _post(client, parent.id, url).status_code == 400
    assert dispatched == []


@pytest.mark.parametrize(
    "url", ["https://example.test/a", "http://www.example.test/a", "EXAMPLE.test/a"]
)
def test_www_and_scheme_equivalence_accepted(client, parent, dispatched, url):
    assert _post(client, parent.id, url).status_code == 201


def test_parent_own_page_400_even_without_scheme_on_parent(client, make_workspace, dispatched):
    brand = make_workspace(company_url="example.test", status="confirmed")
    for url in ("https://www.example.test", "https://example.test/", "example.test/?a=1"):
        resp = _post(client, brand.id, url)
        assert resp.status_code == 400
        assert "Ana sayfa" in resp.json()["detail"]


def test_duplicate_active_focus_409_and_archived_does_not_block(
    client, db_session, parent, dispatched
):
    first = _post(client, parent.id, "https://example.test/hizmetler/a")
    assert first.status_code == 201
    for variant in (
        "https://www.example.test/hizmetler/a/",
        "http://example.test/hizmetler/a?x=1#frag",
        "example.test/hizmetler/a",
    ):
        assert _post(client, parent.id, variant).status_code == 409
    assert _post(client, parent.id, "https://example.test/hizmetler/b").status_code == 201

    row = db_session.get(BrandProfile, first.json()["id"])
    row.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    assert _post(client, parent.id, "https://example.test/hizmetler/a").status_code == 201


# ── miras ──────────────────────────────────────────────────────────


def test_inheritance_copies_policy_and_location_only(client, db_session, parent, dispatched):
    focus_id = _post(client, parent.id, "https://www.example.test/a").json()["id"]
    db_session.expire_all()
    child = db_session.get(BrandProfile, focus_id)
    parent = db_session.get(BrandProfile, parent.id)

    for field in (
        "competitor_terms", "competitor_url_decisions", "competitor_policy", "topic_policy",
        "excluded_info", "default_geo_target_id", "default_language_id",
    ):
        assert getattr(child, field) == getattr(parent, field), field
        assert getattr(child, field) is not None
    assert child.profile_data == LOCATION  # yalniz konum alanlari
    assert child.capabilities is None
    assert child.policy_version == 1
    for secret in ("GizliUrunX", "GizliHizmetY", "GizliSenaryoZ", "GizliSorunW"):
        assert secret not in str(child.profile_data)


def test_inherited_json_is_a_deep_copy(client, db_session, parent, dispatched):
    focus_id = _post(client, parent.id, "https://www.example.test/a").json()["id"]
    db_session.expire_all()
    child = db_session.get(BrandProfile, focus_id)
    child.profile_data["focus_cities"].append("Izmir")
    child.competitor_terms[0]["term"] = "Degisti"
    child.topic_policy["excluded_terms"].append({"term": "yeni"})
    for field in ("profile_data", "competitor_terms", "topic_policy"):
        flag_modified(child, field)
    db_session.commit()
    db_session.expire_all()
    fresh_parent = db_session.get(BrandProfile, parent.id)
    assert fresh_parent.profile_data["focus_cities"] == ["Ankara"]
    assert fresh_parent.competitor_terms[0]["term"] == "Rakip"
    assert fresh_parent.topic_policy["excluded_terms"] == [{"term": "kripto"}]


def test_parent_without_location_gives_focus_without_profile_data(
    client, db_session, make_workspace, dispatched
):
    brand = make_workspace(
        company_url=PARENT_URL, status="confirmed", profile_data={"company_name": "A"}
    )
    focus_id = _post(client, brand.id, "https://www.example.test/a").json()["id"]
    db_session.expire_all()
    assert db_session.get(BrandProfile, focus_id).profile_data is None


# ── analiz gorevleri: konum alanlari + marka baglami ───────────────

GOOD_CONTENT = (
    "Toprak analizi hizmeti. Numune alma, laboratuvar olcumu ve gubreleme onerisi "
    "raporu sunuyoruz. Ciftcilere ozel verim artirici analiz paketleri vardir. "
    "Sonuclar iki is gununde teslim edilir."
)


def _extracted_profile() -> dict:
    # AI semasinda konum alanlari YOK
    return {
        "company_name": "Acme Danismanlik",
        "sector": "Tarim danismanligi",
        "brand_summary": "Toprak analizi",
        "target_audience": "Ciftciler",
        "products": [],
        "services": ["Toprak analizi"],
        "use_cases": ["Gubreleme plani"],
        "problems_solved": ["Verimsiz toprak"],
        "brand_terms": ["Acme"],
        "exclude_themes": [],
        "anchor_texts": ["Toprak analizi"],
    }


class FakeAI:
    collector = None

    def close(self):
        return None


class RecordingExtractor:
    brand_contexts: list = []

    def __init__(self, _ai):
        pass

    def crawl_for_profile_content(self, _url, crawl_scope="site"):
        return {"error": None, "site_content": GOOD_CONTENT, "source_pages": ["https://x.test"]}

    def extract_profile_from_site_content(self, _content, crawl_scope="site", brand_context=None):
        type(self).brand_contexts.append(brand_context)
        return _extracted_profile()

    def extract_profile_from_keywords(self, _site, _keywords, **_kwargs):
        return _extracted_profile()


@pytest.fixture
def fake_stack(monkeypatch):
    RecordingExtractor.brand_contexts = []
    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kw: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", RecordingExtractor)
    yield RecordingExtractor
    RecordingExtractor.brand_contexts = []


def _focus_with_token(client, db_session, parent_id, path="a"):
    focus_id = _post(client, parent_id, f"https://www.example.test/{path}").json()["id"]
    db_session.expire_all()
    row = db_session.get(BrandProfile, focus_id)
    return row, row.analysis_attempt_id


def _reload(workspace_id):
    session = SessionLocal()
    try:
        row = session.get(BrandProfile, workspace_id)
        session.expunge(row)
        return row
    finally:
        session.close()


def test_location_fields_survive_first_analysis(client, db_session, parent, dispatched, fake_stack):
    focus, token = _focus_with_token(client, db_session, parent.id)
    run_profile_analysis_first(
        workspace_id=focus.id, company_url=focus.company_url,
        competitor_urls=None, attempt_id=token,
    )
    row = _reload(focus.id)
    assert row.status == "profile_review"
    for key, value in LOCATION.items():
        assert row.profile_data[key] == value
    assert row.profile_data["services"] == ["Toprak analizi"]


def test_location_fields_survive_profile_from_keywords(
    client, db_session, parent, dispatched, fake_stack
):
    focus, token = _focus_with_token(client, db_session, parent.id, "b")
    brand_profile_api._run_profile_from_keywords(
        workspace_id=focus.id, keywords=["toprak analizi"], attempt_id=token,
    )
    row = _reload(focus.id)
    assert row.status == "draft"
    for key, value in LOCATION.items():
        assert row.profile_data[key] == value


def test_normal_workspace_reanalysis_keeps_its_location_fields(
    make_workspace, fake_stack
):
    ws = make_workspace(
        status="pending", onboarding_flow="profile_first", profile_data=dict(LOCATION),
        analysis_attempt_id="tok",
    )
    run_profile_analysis_first(
        workspace_id=ws.id, company_url=ws.company_url, competitor_urls=None, attempt_id="tok",
    )
    assert _reload(ws.id).profile_data["focus_cities"] == ["Ankara"]


def test_focus_analysis_passes_identity_only_brand_context(
    client, db_session, parent, dispatched, fake_stack
):
    focus, token = _focus_with_token(client, db_session, parent.id)
    run_profile_analysis_first(
        workspace_id=focus.id, company_url=focus.company_url,
        competitor_urls=None, attempt_id=token,
    )
    (context,) = fake_stack.brand_contexts
    assert "Acme Danismanlik" in context and "Tarim danismanligi" in context
    assert "Ciftciler" in context
    for secret in ("GizliUrunX", "GizliHizmetY", "GizliSenaryoZ", "GizliSorunW"):
        assert secret not in context


def test_non_focus_analysis_passes_no_brand_context(make_workspace, fake_stack):
    ws = make_workspace(
        status="pending", onboarding_flow="profile_first", crawl_scope="page",
        analysis_attempt_id="tok",
    )
    run_profile_analysis_first(
        workspace_id=ws.id, company_url=ws.company_url, competitor_urls=None, attempt_id="tok",
    )
    assert fake_stack.brand_contexts == [None]


# ── prompt ─────────────────────────────────────────────────────────


def test_build_brand_context_drops_offering_fields():
    context = build_brand_context(_parent_profile())
    assert context.splitlines() == [
        "- Marka adı: Acme Danismanlik", "- Sektör: Tarim danismanligi", "- Hedef kitle: Ciftciler",
    ]
    assert build_brand_context({"products": ["x"]}) is None
    assert build_brand_context(None) is None


def test_page_prompt_with_context_contains_block_after_rules():
    prompt = build_profile_prompt("içerik", None, "page", "- Marka adı: Acme")
    assert prompt.index(PAGE_SCOPE_RULES) < prompt.index("MARKA BAĞLAMI")
    assert "- Marka adı: Acme" in prompt
    assert "marka bağlamından tamamlama" in prompt


def test_prompts_without_context_are_unchanged():
    plain = PROFILE_EXTRACTION_PROMPT.format(
        site_content="içerik", preliminary_info="(kullanıcı tarafından sağlanmadı)"
    )
    assert build_profile_prompt("içerik", None, "page") == plain + PAGE_SCOPE_RULES
    assert build_profile_prompt("içerik", None, "site") == plain
    # site kapsaminda baglam yok sayilir (bayt-ayni)
    assert build_profile_prompt("içerik", None, "site", "- Marka adı: Acme") == plain


def test_brand_context_does_not_pass_the_source_gate():
    class NeverCalledAI:
        def complete_json(self, *_a, **_kw):
            raise AssertionError("kalite kapisi AI'dan once kapatmali")

    extractor = ProfileExtractor.__new__(ProfileExtractor)
    extractor.ai_service = NeverCalledAI()
    rich_context = "- Marka adı: Acme\n" + "- Sektör: Tarim danismanligi " * 40
    with pytest.raises(CrawlContentUnusable):
        extractor.extract_profile_from_site_content(
            "Yükleniyor...", crawl_scope="page", brand_context=rich_context
        )


# ── 3b: onay akisi ─────────────────────────────────────────────────

ANALYZED = {
    "company_name": "Acme", "sector": "Tarim", "target_audience": "Ciftciler",
    "products": [], "services": ["Toprak analizi"], "use_cases": ["Plan"],
    "problems_solved": ["Verim"], "brand_terms": ["Acme"], "exclude_themes": [],
    "anchor_texts": ["Toprak analizi"],
}


@pytest.fixture
def keyword_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion_from_profile", lambda **kw: calls.append(kw)
    )
    return calls


def _focus_row(make_workspace, parent, status, profile_data):
    return make_workspace(
        company_url="https://www.example.test/a",
        status=status,
        onboarding_flow="profile_first",
        crawl_scope="page",
        parent_brand_profile_id=parent.id,
        profile_data=profile_data,
    )


def _approve(client, workspace_id, **body):
    return client.put(f"{BASE}/workspaces/{workspace_id}/profile/approve", json=body)


def test_focus_first_approval_goes_straight_to_keywords(
    client, make_workspace, parent, keyword_calls
):
    focus = _focus_row(make_workspace, parent, "profile_review", dict(ANALYZED))
    resp = _approve(client, focus.id, rerun_keywords=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["profile_approved_at"] is not None
    assert len(keyword_calls) == 1 and keyword_calls[0]["workspace_id"] == focus.id


def test_focus_reapproval_never_enters_competitor_review(
    client, make_workspace, parent, keyword_calls
):
    for status in ("keywords_review", "failed"):
        focus = _focus_row(make_workspace, parent, status, dict(ANALYZED))
        resp = _approve(client, focus.id, rerun_keywords=False)
        assert resp.status_code == 200
        assert resp.json()["status"] != "competitor_review"
        assert keyword_calls == []


def test_focus_failed_with_location_only_profile_cannot_be_approved(
    client, make_workspace, parent, keyword_calls
):
    focus = _focus_row(make_workspace, parent, "failed", dict(LOCATION))
    assert _approve(client, focus.id).status_code == 400
    assert keyword_calls == []


def test_normal_brand_still_goes_to_competitor_review(client, make_workspace, keyword_calls):
    brand = make_workspace(
        status="profile_review", onboarding_flow="profile_first", profile_data=dict(ANALYZED)
    )
    resp = _approve(client, brand.id, rerun_keywords=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "competitor_review"
    assert keyword_calls == []


# ── izolasyon ──────────────────────────────────────────────────────


def test_focus_cannot_read_parent_runs_and_vice_versa(
    client, make_workspace, make_scoring_run, parent
):
    focus = _focus_row(make_workspace, parent, "confirmed", dict(ANALYZED))
    parent_run = make_scoring_run(brand_profile_id=parent.id)
    focus_run = make_scoring_run(brand_profile_id=focus.id)
    url = "/api/v1/scoring/runs/{}"
    assert client.get(url.format(parent_run.id), params={"brand_profile_id": focus.id}).status_code == 404
    assert client.get(url.format(focus_run.id), params={"brand_profile_id": parent.id}).status_code == 404
    assert client.get(url.format(focus_run.id), params={"brand_profile_id": focus.id}).status_code == 200
