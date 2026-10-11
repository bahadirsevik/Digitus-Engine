"""Deney-geneli bütçe defteri (Codex Faz 1 #1): komutlar arası toplam korunur."""
from __future__ import annotations

import json

import pytest

from app.core.benchmark.faz1_ledger import Faz1Ledger
from app.core.benchmark.reference_characterization import CharacterizationError

CAPS = {"anthropic": 50.0, "gemini": 20.0, "deepseek": 5.0}


def _summary(anth, gem):
    return {"anthropic": {"cost_committed_usd": anth, "cost_settled_usd": anth * 0.9},
            "gemini": {"cost_committed_usd": gem, "cost_settled_usd": gem}}


def test_second_run_inherits_first_spend(tmp_path):
    p = tmp_path / "ledger.json"
    led = Faz1Ledger.open(p)
    assert led.remaining(CAPS) == CAPS
    led.record(kind="hybrid_run", ref="ws1:Aday", budget_summary=_summary(30.0, 5.0))
    led.save()
    led2 = Faz1Ledger.open(p)                       # yeni komut: dosyadan yükler
    rem = led2.remaining(CAPS)
    assert rem["anthropic"] == pytest.approx(20.0) and rem["gemini"] == pytest.approx(15.0)
    assert rem["deepseek"] == 5.0
    led2.record(kind="hybrid_run", ref="ws2:Aday", budget_summary=_summary(20.0, 1.0))
    with pytest.raises(CharacterizationError):      # anthropic tükendi -> çağrı yok
        led2.remaining(CAPS)


def test_committed_not_settled_is_charged(tmp_path):
    led = Faz1Ledger.open(tmp_path / "l.json")
    added = led.record(kind="x", ref="y", budget_summary=_summary(10.0, 0.0))
    assert added["anthropic"] == 10.0 and led.spent("anthropic") == 10.0   # 9.0 degil


def test_tampered_ledger_is_rejected(tmp_path):
    p = tmp_path / "l.json"
    led = Faz1Ledger.open(p)
    led.record(kind="x", ref="y", budget_summary=_summary(1.0, 1.0))
    led.save()
    doc = json.loads(p.read_text("utf-8"))
    doc["spent_usd"]["anthropic"] = 0.0
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CharacterizationError):
        Faz1Ledger.open(p)


def test_summary_without_cost_fails_closed(tmp_path):
    led = Faz1Ledger.open(tmp_path / "l.json")
    with pytest.raises(CharacterizationError):
        led.record(kind="x", ref="y", budget_summary={"anthropic": {"requests_used": 3}})


def test_reserve_persists_before_calls_and_settle_closes(tmp_path):
    """Codex 2. tur: rezervasyon dosyaya cagri ONCESI yazilir; cokme = acik kalir."""
    p = tmp_path / "ledger.json"
    led = Faz1Ledger.open(p)
    rem = led.reserve(kind="hybrid_run", ref="ws1:Aday", caps=CAPS)
    assert rem == CAPS
    # baska bir "process" defteri acar: rezervasyon harcanmis sayilir -> tavan tukendi
    other = Faz1Ledger.open(p)
    with pytest.raises(CharacterizationError):
        other.remaining(CAPS)
    with pytest.raises(CharacterizationError):             # ayni ref ikinci kez acilamaz
        led.reserve(kind="hybrid_run", ref="ws1:Aday", caps=CAPS)
    added = led.settle(ref="ws1:Aday", budget_summary=_summary(3.0, 1.0))
    assert added == {"anthropic": 3.0, "gemini": 1.0}
    again = Faz1Ledger.open(p)
    assert again.doc["pending"] == {} and again.remaining(CAPS)["anthropic"] == pytest.approx(47.0)


def test_crashed_reservation_is_reconciled_by_human(tmp_path):
    p = tmp_path / "ledger.json"
    led = Faz1Ledger.open(p)
    led.reserve(kind="profile_compare", ref="profile:21,30", caps=CAPS)
    crashed = Faz1Ledger.open(p)                            # settle hic cagrilmadi
    with pytest.raises(CharacterizationError):
        crashed.reserve(kind="hybrid_run", ref="ws2:Aday", caps=CAPS)
    crashed.reconcile(ref="profile:21,30", actual_usd={"anthropic": 0.4, "gemini": 0.1})
    assert crashed.remaining(CAPS)["anthropic"] == pytest.approx(49.6)
    assert crashed.reserve(kind="hybrid_run", ref="ws2:Aday", caps=CAPS)["gemini"] == pytest.approx(19.9)


def test_settle_with_none_summary_records_zero(tmp_path):
    led = Faz1Ledger.open(tmp_path / "l.json")
    led.reserve(kind="hybrid_run", ref="x", caps=CAPS)
    assert led.settle(ref="x", budget_summary=None) == {}
    assert led.remaining(CAPS) == CAPS
