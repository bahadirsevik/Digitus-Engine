"""Snapshot tests against the 4 real Google Ads CSV exports in datas/.

These tests verify that the parser handles production-quality files
correctly (encoding, column mapping, Turkish characters, junk rows,
metric ranges). They are intentionally read-only; changing the CSV
files requires updating the expected values here.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from app.core.csv_import.google_ads_parser import parse_google_ads_csv

DATAS_DIR = Path(__file__).resolve().parent.parent.parent / "datas"


def _load(filename: str) -> bytes:
    path = DATAS_DIR / filename
    if not path.exists():
        pytest.skip(f"Test data file not found: {path}")
    return path.read_bytes()


# ── KW-0: 90 keywords, no junk rows ───────────────────────────────────────────

class TestKW0:
    def setup_method(self):
        self.result = parse_google_ads_csv(_load("Google Ads KW-0.csv"), filename="KW-0.csv")

    def test_meta(self):
        m = self.result.meta
        assert m.encoding == "utf-16-le"
        assert m.header_row == 3
        assert m.detected_separator == "tab"
        assert m.matched_signals >= 4
        assert m.source_format_valid is True

    def test_keyword_count(self):
        assert len(self.result.keywords) == 90

    def test_no_junk_rows(self):
        assert len(self.result.junk_rows) == 0

    def test_first_keyword_metrics(self):
        k = self.result.keywords[0]
        assert k.keyword == "google ads ajansı"  # "google ads ajansı"
        assert k.monthly_volume == 320
        assert k.competition_index == 51
        assert float(k.competition_score) == pytest.approx(0.51, abs=0.01)
        assert float(k.trend_3m) == pytest.approx(-64.0, abs=0.1)
        assert float(k.trend_12m) == pytest.approx(-19.0, abs=0.1)

    def test_no_replacement_characters(self):
        bad = [k.keyword for k in self.result.keywords if "�" in k.keyword]
        assert bad == [], f"Replacement chars found in: {bad}"

    def test_all_volumes_at_least_one(self):
        zero = [k.keyword for k in self.result.keywords if k.monthly_volume < 1]
        assert zero == []

    def test_competition_scores_in_range(self):
        for k in self.result.keywords:
            assert 0.0 <= float(k.competition_score) <= 1.0, (
                f"{k.keyword}: competition_score={k.competition_score} out of range"
            )


# ── KW-1: 800 keywords, 1 junk row ("anahtar kelime") ─────────────────────────

class TestKW1:
    def setup_method(self):
        self.result = parse_google_ads_csv(_load("Google Ads KW-1.csv"), filename="KW-1.csv")

    def test_meta(self):
        m = self.result.meta
        assert m.encoding == "utf-16-le"
        assert m.header_row == 3
        assert m.detected_separator == "tab"
        assert m.source_format_valid is True

    def test_keyword_count(self):
        assert len(self.result.keywords) == 800

    def test_junk_row_count_and_reason(self):
        assert len(self.result.junk_rows) == 1
        assert self.result.junk_rows[0]["keyword"] == "anahtar kelime"
        assert self.result.junk_rows[0]["reason"] == "junk_row"

    def test_first_keyword_metrics(self):
        k = self.result.keywords[0]
        assert k.keyword == "google ads reklam verme"
        assert k.monthly_volume == 1600
        assert k.competition_index == 73
        assert float(k.competition_score) == pytest.approx(0.73, abs=0.01)
        assert float(k.trend_12m) == pytest.approx(-32.0, abs=0.1)

    def test_no_replacement_characters(self):
        bad = [k.keyword for k in self.result.keywords if "�" in k.keyword]
        assert bad == []

    def test_all_volumes_at_least_one(self):
        assert all(k.monthly_volume >= 1 for k in self.result.keywords)

    def test_competition_scores_in_range(self):
        for k in self.result.keywords:
            assert 0.0 <= float(k.competition_score) <= 1.0


# ── KW-2: 210 keywords, no junk rows ──────────────────────────────────────────

class TestKW2:
    def setup_method(self):
        self.result = parse_google_ads_csv(_load("Google Ads KW-2.csv"), filename="KW-2.csv")

    def test_meta(self):
        m = self.result.meta
        assert m.encoding == "utf-16-le"
        assert m.header_row == 3
        assert m.detected_separator == "tab"
        assert m.source_format_valid is True

    def test_keyword_count(self):
        assert len(self.result.keywords) == 210

    def test_no_junk_rows(self):
        assert len(self.result.junk_rows) == 0

    def test_first_keyword_metrics(self):
        k = self.result.keywords[0]
        assert k.keyword == "google ads reklam verme"
        assert k.monthly_volume == 1600
        assert k.competition_index == 73
        assert float(k.competition_score) == pytest.approx(0.73, abs=0.01)
        assert float(k.trend_3m) == pytest.approx(0.0, abs=0.1)

    def test_no_replacement_characters(self):
        bad = [k.keyword for k in self.result.keywords if "�" in k.keyword]
        assert bad == []

    def test_all_volumes_at_least_one(self):
        assert all(k.monthly_volume >= 1 for k in self.result.keywords)

    def test_competition_scores_in_range(self):
        for k in self.result.keywords:
            assert 0.0 <= float(k.competition_score) <= 1.0


# ── KW-3: 796 keywords, 1 junk row ("anahtar kelime") ─────────────────────────

class TestKW3:
    def setup_method(self):
        self.result = parse_google_ads_csv(_load("Google Ads KW-3.csv"), filename="KW-3.csv")

    def test_meta(self):
        m = self.result.meta
        assert m.encoding == "utf-16-le"
        assert m.header_row == 3
        assert m.detected_separator == "tab"
        assert m.source_format_valid is True

    def test_keyword_count(self):
        assert len(self.result.keywords) == 796

    def test_junk_row_count_and_reason(self):
        assert len(self.result.junk_rows) == 1
        assert self.result.junk_rows[0]["keyword"] == "anahtar kelime"
        assert self.result.junk_rows[0]["reason"] == "junk_row"

    def test_first_keyword_metrics(self):
        k = self.result.keywords[0]
        assert k.keyword == "google reklam verme"
        assert k.monthly_volume == 8100
        assert k.competition_index == 74
        assert float(k.competition_score) == pytest.approx(0.74, abs=0.01)
        assert float(k.trend_3m) == pytest.approx(23.0, abs=0.1)
        assert float(k.trend_12m) == pytest.approx(-18.0, abs=0.1)

    def test_no_replacement_characters(self):
        bad = [k.keyword for k in self.result.keywords if "�" in k.keyword]
        assert bad == []

    def test_all_volumes_at_least_one(self):
        assert all(k.monthly_volume >= 1 for k in self.result.keywords)

    def test_competition_scores_in_range(self):
        for k in self.result.keywords:
            assert 0.0 <= float(k.competition_score) <= 1.0
