from app.api.v1 import brand_profile as brand_profile_api


def test_workspace_keyword_approve_stores_cleaned_keywords_and_starts_profile_generation(
    client,
    make_workspace,
    monkeypatch,
):
    workspace = make_workspace(
        status="keywords_review",
        suggested_keywords=["hisse analiz", "borsa takip"],
        profile_data=None,
    )
    calls = []

    def fake_run_profile_from_keywords(workspace_id, keywords, attempt_id):
        calls.append((workspace_id, keywords))

    monkeypatch.setattr(brand_profile_api, "_run_profile_from_keywords", fake_run_profile_from_keywords)

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": [" Hisse Analiz ", "hisse analiz", "", "teknik analiz"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["suggested_keywords"] == ["Hisse Analiz", "teknik analiz"]
    assert calls == [(workspace.id, ["Hisse Analiz", "teknik analiz"])]


def test_workspace_keyword_approve_rejects_wrong_status(client, make_workspace):
    workspace = make_workspace(status="draft", suggested_keywords=["hisse analiz"])

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["teknik analiz"]},
    )

    assert response.status_code == 400


def test_workspace_confirm_regenerates_anchor_and_ignores_manual_anchor_payload(
    client,
    make_workspace,
):
    workspace = make_workspace(
        status="confirmed",
        profile_data={
            "company_name": "Hissefy",
            "sector": "Finansal teknoloji",
            "target_audience": "Yatirimcilar",
            "products": ["Analiz Platformu"],
            "services": [],
            "use_cases": ["Borsa takibi", "Portfoy izleme"],
            "problems_solved": ["Dagitik veri"],
            "brand_terms": ["Hissefy"],
            "exclude_themes": [],
            "anchor_texts": ["Borsa takibi Portfoy izleme"],
        },
    )

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/confirm",
        json={
            "profile_data": {
                "use_cases": ["Portfoy izleme"],
                "anchor_texts": ["MANUEL ANCHOR KULLANILMAMALI"],
            }
        },
    )

    assert response.status_code == 200
    profile = response.json()["profile_data"]
    blob = " ".join(profile["anchor_texts"]).lower()
    assert "manuel anchor" not in blob
    assert "borsa takibi" not in blob
    assert "portfoy izleme" in blob
