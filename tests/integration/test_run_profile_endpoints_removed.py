"""Run-kapsamlı profil analiz/onay uçları kaldırıldı (plan_yapilacaklar 1.1).

`PUT /brand-profile/runs/{run_id}/profile/confirm` workspace kapsamı olmadan
ScoringRun yüklüyordu (izolasyon deliği); `POST .../profile/analyze` da UI
tarafından kullanılmıyordu. Profil akışı workspace uçlarından yürür. Bu test
iki rotanın geri gelmediğini ve salt-okunur `GET .../profile` ucunun
etkilenmediğini sabitler.
"""
from app.main import app

REMOVED = [
    ("POST", "/api/v1/brand-profile/runs/{run}/profile/analyze"),
    ("PUT", "/api/v1/brand-profile/runs/{run}/profile/confirm"),
]


def test_removed_routes_not_registered():
    registered = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", None) or ()
    }
    for method, path in REMOVED:
        assert (method, path.replace("{run}", "{run_id}")) not in registered
    # Kapsam dışı uç yerinde kalır
    assert ("GET", "/api/v1/brand-profile/runs/{run_id}/profile") in registered


def test_removed_routes_return_404_or_405(client, make_workspace,
                                           make_scoring_run):
    ws = make_workspace(name="removed-ep-ws")
    run = make_scoring_run(brand_profile_id=ws.id, algorithm_version="v3")
    q = f"brand_profile_id={ws.id}"
    for method, path in REMOVED:
        url = path.format(run=run.id) + f"?{q}"
        resp = client.request(method, url, json={"company_url": "https://x.test",
                                                 "profile_data": {}})
        assert resp.status_code in (404, 405), (method, url, resp.status_code)
