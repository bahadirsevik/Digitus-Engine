import pytest

from app.core.csv_import.google_ads_parser import GoogleAdsCSVError, parse_google_ads_csv


# ── helpers ────────────────────────────────────────────────────────────────────

def _csv_bytes(*rows: str, encoding: str = "utf-16-le", header: str | None = None) -> bytes:
    """Build a minimal Google Ads-style CSV in the given encoding."""
    hdr = header or (
        "Keyword\tAvg. monthly searches\tThree month change\tYoY change\t"
        "Competition\tCompetition (indexed value)\tTop of page bid (high range)"
    )
    text = (
        "Keyword Stats 2025\n"
        "1 Ocak 2025 - 31 Aralik 2025\n"
        + hdr
        + "\n"
        + "\n".join(rows)
    )
    return text.encode(encoding)


# ── existing tests (unchanged) ────────────────────────────────────────────────

def test_google_ads_parser_decodes_utf16_and_finds_header_row():
    result = parse_google_ads_csv(
        _csv_bytes("organik sampuan\t1.200\t12%\t-5%\tHigh\t72\t15,50"),
        filename="gads.csv",
    )
    assert result.meta.encoding == "utf-16-le"
    assert result.meta.header_row == 3
    assert result.meta.detected_separator == "tab"
    assert result.keywords[0].keyword == "organik sampuan"


def test_google_ads_parser_maps_metrics_and_skips_junk_header_rows():
    result = parse_google_ads_csv(
        _csv_bytes(
            "organik sampuan\t1.200\t12%\t-5%\tHigh\t72\t15,50",
            "Keyword\tAvg. monthly searches\tThree month change\tYoY change\tCompetition\tCompetition (indexed value)\tTop of page bid (high range)",
            "bos hacim\t0\t∞\t10%\tDüşük\t\t1,25",
        ),
        filename="gads.csv",
    )
    assert len(result.keywords) == 2
    first = result.keywords[0]
    assert first.monthly_volume == 1200
    assert float(first.trend_3m) == 12
    assert float(first.trend_12m) == -5
    assert float(first.competition_score) == 0.72
    assert first.competition_index == 72
    assert float(first.top_bid_high) == 15.5

    second = result.keywords[1]
    assert second.monthly_volume == 1
    assert float(second.trend_3m) == 0
    assert float(second.competition_score) == 0.2
    assert len(result.junk_rows) == 1


# ── new encoding tests ─────────────────────────────────────────────────────────

def test_google_ads_parser_decodes_utf16_be():
    content = b"\xfe\xff" + _csv_bytes(
        "kelime be\t800\t3%\t6%\tMedium\t50\t5,00", encoding="utf-16-be"
    )
    result = parse_google_ads_csv(content, filename="be.csv")
    assert result.meta.encoding == "utf-16-be"
    assert result.keywords[0].keyword == "kelime be"
    assert result.keywords[0].monthly_volume == 800


def test_google_ads_parser_decodes_utf8_bom():
    content = b"\xef\xbb\xbf" + _csv_bytes(
        "bom keyword\t750\t3%\t7%\tLow\t20\t2,00", encoding="utf-8"
    )
    result = parse_google_ads_csv(content, filename="bom.csv")
    assert result.meta.encoding == "utf-8-sig"
    assert result.keywords[0].keyword == "bom keyword"
    assert result.keywords[0].monthly_volume == 750


def test_google_ads_parser_decodes_utf8_plain():
    content = _csv_bytes("plain keyword\t300\t1%\t2%\tHigh\t80\t12,00", encoding="utf-8")
    result = parse_google_ads_csv(content, filename="plain.csv")
    assert result.meta.encoding == "utf-8"
    assert result.keywords[0].keyword == "plain keyword"


# ── header detection ──────────────────────────────────────────────────────────

def test_google_ads_parser_raises_on_file_with_no_recognisable_header():
    content = "sadece metin\nbir seyler\nbaska seyler\ndorduncu satir\n".encode("utf-8")
    with pytest.raises(GoogleAdsCSVError):
        parse_google_ads_csv(content, filename="bad.csv")


def test_google_ads_parser_skips_header_search_beyond_10_lines():
    """Header at line 11 (>first 10 scanned) must raise an error."""
    preamble = "\n".join(f"preamble row {i}" for i in range(11))
    header = (
        "Keyword\tAvg. monthly searches\tThree month change\tYoY change\t"
        "Competition\tCompetition (indexed value)\n"
    )
    content = (preamble + "\n" + header + "kw\t100\t1%\t2%\tLow\t20\n").encode("utf-8")
    with pytest.raises(GoogleAdsCSVError):
        parse_google_ads_csv(content, filename="deep.csv")


# ── junk-row detection ────────────────────────────────────────────────────────

def test_google_ads_parser_marks_numeric_only_row_as_junk():
    result = parse_google_ads_csv(
        _csv_bytes(
            "1.200\t500\t5%\t10%\tMedium\t50\t",   # all-digit keyword
            "real keyword\t400\t2%\t4%\tLow\t20\t",
        ),
        filename="junk.csv",
    )
    assert any(j["keyword"] == "1.200" for j in result.junk_rows)
    assert len(result.keywords) == 1
    assert result.keywords[0].keyword == "real keyword"


def test_google_ads_parser_marks_date_row_as_junk():
    """Rows matching 'DD Month YYYY' pattern are junk."""
    result = parse_google_ads_csv(
        _csv_bytes(
            "1 Ocak 2025\t\t\t\t\t\t",
            "real keyword\t400\t2%\t4%\tHigh\t75\t",
        ),
        filename="date.csv",
    )
    junk_kws = [j["keyword"] for j in result.junk_rows]
    assert "1 Ocak 2025" in junk_kws
    assert result.keywords[0].keyword == "real keyword"


# ── metric parsing ────────────────────────────────────────────────────────────

def test_google_ads_parser_infinity_trend_becomes_zero():
    result = parse_google_ads_csv(
        _csv_bytes("inf kelime\t500\t∞\t∞\tMedium\t50\t"),
        filename="inf.csv",
    )
    kw = result.keywords[0]
    assert float(kw.trend_3m) == 0.0
    assert float(kw.trend_12m) == 0.0


def test_google_ads_parser_competition_label_high_medium_low():
    # Only competition label column; no indexed value
    hdr = "Keyword\tAvg. monthly searches\tThree month change\tYoY change\tCompetition"
    result = parse_google_ads_csv(
        _csv_bytes(
            "high kw\t500\t1%\t2%\tHigh",
            "medium kw\t300\t1%\t2%\tMedium",
            "low kw\t200\t1%\t2%\tLow",
            header=hdr,
            encoding="utf-8",
        ),
        filename="label.csv",
    )
    by_kw = {k.keyword: k for k in result.keywords}
    assert float(by_kw["high kw"].competition_score) == 0.80
    assert float(by_kw["medium kw"].competition_score) == 0.50
    assert float(by_kw["low kw"].competition_score) == 0.20


def test_google_ads_parser_turkish_competition_labels():
    hdr = "Anahtar Kelime\tOrt. Aylık Aramalar\tÜç Aylık Değişim\tYıldan Yıla Değişim\tRekabet"
    result = parse_google_ads_csv(
        _csv_bytes(
            "yüksek kw\t500\t1%\t2%\tYüksek",
            "orta kw\t300\t1%\t2%\tOrta",
            "dusuk kw\t200\t1%\t2%\tDüşük",
            header=hdr,
            encoding="utf-8",
        ),
        filename="tr.csv",
    )
    by_kw = {k.keyword: k for k in result.keywords}
    assert float(by_kw["yüksek kw"].competition_score) == 0.80
    assert float(by_kw["orta kw"].competition_score) == 0.50
    assert float(by_kw["dusuk kw"].competition_score) == 0.20


# ── separator detection ───────────────────────────────────────────────────────

def test_google_ads_parser_semicolon_separator():
    hdr = "Keyword;Avg. monthly searches;Competition;Three month change"
    result = parse_google_ads_csv(
        _csv_bytes("semicolon kw;800;High;5%", header=hdr, encoding="utf-8"),
        filename="semi.csv",
    )
    assert result.meta.detected_separator == "semicolon"
    assert result.keywords[0].keyword == "semicolon kw"
    assert result.keywords[0].monthly_volume == 800
    assert float(result.keywords[0].competition_score) == 0.80
