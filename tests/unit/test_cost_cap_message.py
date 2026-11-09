"""Maliyet sınırı mesajı: ayrılan bütçe 'harcanan' diye gösterilmez."""
from types import SimpleNamespace

from app.core import trial_authorization as ta


def test_cost_cap_message_separates_spent_and_reserved(monkeypatch):
    monkeypatch.setattr(ta, "assignment_cost_cap_usd", lambda ws: 8.0)
    monkeypatch.setattr(
        ta,
        "workspace_cost_usd",
        lambda db, ws_id: {
            "usage_spent_usd": 3.0,
            "settled_ledger_usd": 3.0,
            "spent_usd": 3.0,
            "reserved_usd": 5.0,
            "exposure_usd": 8.0,
        },
    )
    monkeypatch.setattr(ta, "cap_scope", lambda ws: "ledger")

    result = ta.check_cost_cap(None, SimpleNamespace(id=1), stage="create")

    assert result["code"] == ta.COST_CAP_EXCEEDED
    msg = result["message"]
    assert "harcanan $3.0" in msg
    assert "ayrılan $5.0" in msg
    assert "toplam $8.0" in msg
    assert "harcanan $8.0" not in msg
