"""Plan v13 entegrasyon testleri — policy review + freshness + guard'lar.

Gerçek Postgres (docker-compose.test.yml) ister. Kapsam:
- PUT /policy/review: kararlar (block/not_competitor), kalıcılık
  (competitor_url_decisions), tam-replacement/400 sınırları, durum matrisi
- excluded_info → topic_policy senkronu + exclude_themes'ten gerçek çıkarma
- policy_version artışı + invalidate_workspace_outputs (ContentOutput dahil)
- merkezi POLICY_STALE guard'ı (generation/export 409)
- preview task workspace sahipliği (404)
- eski completed ExportJob (snapshot NULL) → indirilebilir + policy_outdated
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest  # noqa: E402

from app.database.models import (  # noqa: E402
    BrandProfile,
    ChannelPool,
    ContentOutput,
    ExportJob,
    TaskResult,
)


def _review(client, ws_id, payload):
    return client.put(
        f"/api/v1/brand-profile/workspaces/{ws_id}/policy/review", json=payload
    )


def _get_policy(client, ws_id):
    return client.get(f"/api/v1/brand-profile/workspaces/{ws_id}/policy").json()


@pytest.fixture
def confirmed_ws(make_workspace):
    return make_workspace(
        "Policy WS",
        status="confirmed",
        competitor_urls=["https://fintables.com"],
        profile_data={"company_name": "X", "sector": "fin",
                      "exclude_themes": ["ai teması"], "anchor_texts": ["x hisse"]},
    )


class TestPolicyReviewEndpoint:
    def test_block_decision_creates_url_backed_term(self, client, confirmed_ws):
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "Fintables", "decision": "block"},
            ],
        })
        assert res.status_code == 200, res.text
        policy = res.json()
        terms = {t["term"]: t for t in policy["competitor_terms"]}
        assert terms["Fintables"]["status"] == "approved"
        assert terms["Fintables"]["source_urls"] == ["fintables.com"]
        assert terms["Fintables"]["manual_approved"] is False
        # Kalıcı karar kaydı (yeniden açmada UI bundan türetir)
        decision = policy["competitor_url_decisions"]["fintables.com"]
        assert decision["decision"] == "block" and decision["term"] == "Fintables"

    def test_not_competitor_persists_and_releases(self, client, confirmed_ws):
        _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "Fintables", "decision": "block"},
            ],
        })
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "", "decision": "not_competitor"},
            ],
        })
        assert res.status_code == 200, res.text
        policy = res.json()
        approved = [t for t in policy["competitor_terms"] if t["status"] == "approved"]
        assert approved == []
        # Karar kalıcı — yeniden açmada 'block' varsayılanına DÖNMEZ
        assert (
            policy["competitor_url_decisions"]["fintables.com"]["decision"]
            == "not_competitor"
        )

    def test_full_replacement_contract(self, client, confirmed_ws, db_session):
        confirmed_ws.competitor_urls = ["https://a-firm.com", "https://b-firm.com"]
        db_session.commit()
        # Eksik karar kümesi → 400 (sessizce eski kararları koruyamaz)
        res = _review(client, confirmed_ws.id, {
            "competitor_decisions": [
                {"url": "https://a-firm.com", "term": "A", "decision": "block"},
            ],
        })
        assert res.status_code == 400

    def test_validation_errors(self, client, confirmed_ws):
        # block + boş term → 400 (bozuk istemci bloğu sessizce kaldıramaz)
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "  ", "decision": "block"},
            ],
        })
        assert res.status_code == 400
        # geçersiz URL → 400; iki bozuk URL boş anahtar üzerinden eşleşemez
        res = _review(client, confirmed_ws.id, {"competitor_urls": ["not a url !!"]})
        assert res.status_code == 400
        # normalize-sonrası tekrar → 400
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com", "https://www.fintables.com/"],
        })
        assert res.status_code == 400
        # karar URL'si listede yok → 400
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://baska.com", "term": "X", "decision": "block"},
            ],
        })
        assert res.status_code == 400

    def test_url_removal_cleans_terms_and_decisions(self, client, confirmed_ws):
        _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "Fintables", "decision": "block"},
            ],
        })
        res = _review(client, confirmed_ws.id, {"competitor_urls": []})
        assert res.status_code == 200
        policy = res.json()
        approved = [t for t in policy["competitor_terms"] if t["status"] == "approved"]
        assert approved == []
        assert policy["competitor_url_decisions"] == {}

    def test_status_matrix(self, client, make_workspace):
        running = make_workspace("Running WS", status="running")
        res = _review(client, running.id, {"excluded_info": "kripto"})
        assert res.status_code == 409  # background analiz sürerken 409

    def test_excluded_info_single_source_of_truth(self, client, confirmed_ws, db_session):
        res = _review(client, confirmed_ws.id, {"excluded_info": "temettü takibi"})
        assert res.status_code == 200
        policy = res.json()
        topic = {e["term"]: e["status"] for e in policy["topic_policy"]["excluded_terms"]}
        assert topic["temettü takibi"] == "approved"

        # Kaldırınca hem topic rejected hem exclude_themes'ten çıkar
        res = _review(client, confirmed_ws.id, {"excluded_info": ""})
        assert res.status_code == 200
        policy = res.json()
        topic = {e["term"]: e["status"] for e in policy["topic_policy"]["excluded_terms"]}
        assert topic["temettü takibi"] == "rejected"
        db_session.refresh(confirmed_ws)
        themes = (confirmed_ws.profile_data or {}).get("exclude_themes", [])
        assert "temettü takibi" not in themes
        # AI teması korunur (kullanıcı ONA dokunmadı)
        assert "ai teması" in themes


class TestVersioningAndStale:
    def test_policy_change_bumps_version_and_invalidates(
        self, client, confirmed_ws, db_session, make_scoring_run, make_keyword
    ):
        run = make_scoring_run(brand_profile_id=confirmed_ws.id, status="channel_assigned")
        run.channel_pool_policy_version = confirmed_ws.policy_version or 1
        kw = make_keyword("fintables abonelik", brand_profile_id=confirmed_ws.id)
        db_session.add(ContentOutput(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            content_type="blog_post", content_data={"title": "x"}, is_stale=False,
        ))
        db_session.commit()
        v0 = confirmed_ws.policy_version or 1

        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "Fintables", "decision": "block"},
            ],
        })
        assert res.status_code == 200
        db_session.expire_all()
        ws = db_session.get(BrandProfile, confirmed_ws.id)
        assert ws.policy_version == v0 + 1
        # Mevcut içerik fiilen kullanımdan düştü (yalnız uyarı değil)
        content = db_session.query(ContentOutput).filter(
            ContentOutput.scoring_run_id == run.id
        ).one()
        assert content.is_stale is True

    def test_stale_run_blocks_generation_and_export(
        self, client, confirmed_ws, db_session, make_scoring_run, make_keyword
    ):
        run = make_scoring_run(brand_profile_id=confirmed_ws.id, status="channel_assigned")
        run.channel_pool_policy_version = (confirmed_ws.policy_version or 1) + 5  # != → stale
        kw = make_keyword("hisse takip", brand_profile_id=confirmed_ws.id)
        db_session.add(ChannelPool(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO", final_rank=1,
        ))
        db_session.commit()

        res = client.post(
            f"/api/v1/generation/seo-geo/bulk/{run.id}",
            params={"brand_profile_id": confirmed_ws.id},
        )
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "POLICY_STALE"

        res = client.post(
            "/api/v1/export/",
            params={"brand_profile_id": confirmed_ws.id},
            json={"scoring_run_id": run.id, "format": "excel"},
        )
        assert res.status_code == 409

    def test_no_effective_change_no_bump(self, client, confirmed_ws, db_session):
        v0 = confirmed_ws.policy_version or 1
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
        })
        assert res.status_code == 200
        db_session.expire_all()
        ws = db_session.get(BrandProfile, confirmed_ws.id)
        assert ws.policy_version == v0


class TestPreviewOwnership:
    def test_cross_workspace_preview_404(self, client, make_workspace, db_session):
        ws_a = make_workspace("WS A", status="confirmed")
        ws_b = make_workspace("WS B", status="confirmed")
        db_session.add(TaskResult(
            task_id="prev-123", task_type="policy_preview",
            brand_profile_id=ws_a.id, status="completed",
            result_data={"competitors": []},
        ))
        db_session.commit()
        res = client.get(
            f"/api/v1/brand-profile/workspaces/{ws_b.id}"
            f"/policy/competitors/preview/prev-123"
        )
        assert res.status_code == 404
        res = client.get(
            f"/api/v1/brand-profile/workspaces/{ws_a.id}"
            f"/policy/competitors/preview/prev-123"
        )
        assert res.status_code == 200


class TestExportPolicyOutdated:
    def test_null_snapshot_completed_downloadable_and_flagged(
        self, client, confirmed_ws, db_session, make_scoring_run, tmp_path
    ):
        run = make_scoring_run(brand_profile_id=confirmed_ws.id, status="completed")
        filepath = tmp_path / "old_report.xlsx"
        filepath.write_bytes(b"eski rapor")
        db_session.add(ExportJob(
            id="00000000-0000-0000-0000-000000000001",
            brand_profile_id=confirmed_ws.id,
            scoring_run_id=run.id,
            status="completed", progress=100,
            format="excel", file_name="old_report.xlsx",
            filepath=str(filepath),
            requested_policy_version=None,  # migration öncesi kayıt
        ))
        db_session.commit()

        res = client.get(
            "/api/v1/export/00000000-0000-0000-0000-000000000001/status",
            params={"brand_profile_id": confirmed_ws.id},
        )
        assert res.status_code == 200
        assert res.json()["policy_outdated"] is True

        res = client.get(
            "/api/v1/export/00000000-0000-0000-0000-000000000001/download",
            params={"brand_profile_id": confirmed_ws.id},
        )
        assert res.status_code == 200  # tarihsel artefakt İNDİRİLEBİLİR kalır


class TestWorkspaceResponseContract:
    """Codex blocker'ı: liste/detay yanıtları competitor_urls +
    competitor_url_decisions TAŞIMAZSA Rakipler kartı yeni oturumda boş
    başlar ve tam-replacement kaydı mevcut kararları siler."""

    def test_reopen_carries_urls_and_decisions(self, client, confirmed_ws):
        res = _review(client, confirmed_ws.id, {
            "competitor_urls": ["https://fintables.com"],
            "competitor_decisions": [
                {"url": "https://fintables.com", "term": "",
                 "decision": "not_competitor"},
            ],
        })
        assert res.status_code == 200, res.text

        # Detay yanıtı (yeniden açılış): URL'ler + kalıcı karar gelmeli
        detail = client.get(
            f"/api/v1/brand-profile/workspaces/{confirmed_ws.id}"
        ).json()
        assert detail["competitor_urls"] == ["https://fintables.com"]
        assert (
            detail["competitor_url_decisions"]["fintables.com"]["decision"]
            == "not_competitor"
        )

        # Liste yanıtı (kart açılışı buradan beslenir): aynı sözleşme
        rows = client.get("/api/v1/brand-profile/workspaces").json()
        row = next(r for r in rows if r["id"] == confirmed_ws.id)
        assert row["competitor_urls"] == ["https://fintables.com"]
        assert (
            row["competitor_url_decisions"]["fintables.com"]["decision"]
            == "not_competitor"
        )


class TestApproveProfileDecisions:
    def test_onboarding_approve_carries_decisions(self, client, make_workspace, db_session):
        ws = make_workspace(
            "Onboard WS",
            status="profile_review",
            onboarding_flow="profile_first",
            competitor_urls=["https://fintables.com"],
            profile_data={"company_name": "X", "sector": "fin",
                          "products": ["a"], "anchor_texts": ["x"]},
        )
        res = client.put(
            f"/api/v1/brand-profile/workspaces/{ws.id}/profile/approve",
            json={
                "competitor_urls": ["https://fintables.com"],
                "competitor_decisions": [
                    {"url": "https://fintables.com", "term": "Fintables",
                     "decision": "block"},
                ],
                "rerun_keywords": False,
            },
        )
        assert res.status_code == 200, res.text
        db_session.expire_all()
        ws = db_session.get(BrandProfile, ws.id)
        approved = [
            t["term"] for t in (ws.competitor_terms or [])
            if t.get("status") == "approved"
        ]
        assert approved == ["Fintables"]
        assert "fintables.com" in (ws.competitor_url_decisions or {})
