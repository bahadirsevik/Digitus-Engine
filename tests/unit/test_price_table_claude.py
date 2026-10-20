"""Claude fiyat satirlari (2026-10-11) — tam sayilar ve kismi eslesme yok."""
import pytest

from app.core.telemetry.usage import ANTHROPIC_PRICE_SOURCE, PRICE_TABLE, price_for

_EXPECTED = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}


@pytest.mark.parametrize("model,prices", _EXPECTED.items())
def test_claude_prices_exact(model, prices):
    p = price_for(model)
    assert (p["input"], p["output"], p["input_cache_hit"]) == prices
    assert p["currency"] == "USD" and p["valid_from"] == "2026-10-11"
    assert "valid_until" not in p
    assert set(PRICE_TABLE[model]) == {
        "currency", "input_per_m", "input_cache_hit_per_m", "output_per_m", "valid_from",
    }


def test_no_partial_matching():
    assert price_for("claude-haiku-5") is None


def test_source_constant():
    assert ANTHROPIC_PRICE_SOURCE.startswith("https://platform.claude.com/")
