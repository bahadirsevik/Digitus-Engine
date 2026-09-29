"""Google Ads CSV parser for keyword imports.

The supported source is Google Ads / Keyword Planner exports. These files can
contain metadata rows before the actual header and may be UTF-16 encoded.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


HEADER_SIGNALS = (
    "keyword",
    "anahtar kelime",
    "avg. monthly searches",
    "monthly searches",
    "ort. aylik aramalar",
    "ort. aylık aramalar",
    "competition",
    "rekabet",
    "three month change",
    "uc aylik",
    "üç aylık",
    "yoy change",
    "year over year",
    "yildan yila",
    "yıldan yıla",
)

JUNK_PATTERNS = (
    re.compile(r"^keyword$", re.IGNORECASE),
    re.compile(r"^anahtar\s+kelime$", re.IGNORECASE),
    re.compile(r"^keyword\s+stats", re.IGNORECASE),
    re.compile(r"^\d{1,2}\s+\w+\s+\d{4}", re.IGNORECASE),
)


@dataclass(frozen=True)
class ParserMeta:
    parser_type: str
    encoding: str
    header_row: int
    detected_separator: str
    matched_signals: int
    source_format_valid: bool


@dataclass(frozen=True)
class ParsedKeyword:
    keyword: str
    source_file: str
    source_row: int
    monthly_volume: int
    trend_3m: Decimal
    trend_12m: Decimal
    competition_score: Decimal
    competition_index: int | None = None
    top_bid_low: Decimal | None = None
    top_bid_high: Decimal | None = None
    data_source: str = "csv"
    sector: str | None = None
    target_market: str | None = None
    geo_target_id: str | None = None
    language_id: str | None = None

    def to_keyword_data(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "monthly_volume": self.monthly_volume,
            "trend_3m": self.trend_3m,
            "trend_12m": self.trend_12m,
            "competition_score": self.competition_score,
            "data_source": self.data_source,
            "sector": self.sector,
            "target_market": self.target_market,
            "geo_target_id": self.geo_target_id,
            "language_id": self.language_id,
            "_source_file": self.source_file,
            "_source_row": self.source_row,
            "_competition_index": self.competition_index,
            "_top_bid_low": self.top_bid_low,
            "_top_bid_high": self.top_bid_high,
        }


@dataclass(frozen=True)
class GoogleAdsParseResult:
    meta: ParserMeta
    keywords: list[ParsedKeyword]
    junk_rows: list[dict[str, Any]]


class GoogleAdsCSVError(ValueError):
    """Raised when an uploaded file is not a supported Google Ads export."""


def parse_google_ads_csv(content: bytes, filename: str = "upload.csv") -> GoogleAdsParseResult:
    text, encoding = _decode_content(content)
    lines = [line.strip("\ufeff\r") for line in text.splitlines() if line.strip()]
    if not lines:
        raise GoogleAdsCSVError("CSV dosyası boş.")

    header_index, separator, headers, matched_signals = _find_header(lines)
    if header_index is None or separator is None or headers is None:
        raise GoogleAdsCSVError(
            "Google Ads CSV başlığı bulunamadı. Lütfen Keyword Planner export dosyası yükleyin."
        )

    col_map = _build_column_map(headers)
    if "keyword" not in col_map or "monthly_volume" not in col_map:
        raise GoogleAdsCSVError(
            "Google Ads CSV kolonları eksik. Keyword ve Avg. monthly searches kolonları gerekli."
        )

    keywords: list[ParsedKeyword] = []
    junk_rows: list[dict[str, Any]] = []

    for row_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        cols = _split_line(line, separator)
        keyword = _get(cols, col_map.get("keyword")).strip().strip('"')
        if _is_junk_keyword(keyword):
            junk_rows.append(
                {"keyword": keyword, "source_file": filename, "source_row": row_number, "reason": "junk_row"}
            )
            continue

        monthly_volume = max(1, _parse_int(_get(cols, col_map.get("monthly_volume")), default=1))
        trend_3m = _parse_decimal(_get(cols, col_map.get("trend_3m")), default=Decimal("0"))
        trend_12m = _parse_decimal(_get(cols, col_map.get("trend_12m")), default=Decimal("0"))
        competition_index = _parse_optional_int(_get(cols, col_map.get("competition_index")))
        competition_score = _competition_score(
            competition_index,
            _get(cols, col_map.get("competition_label")),
        )
        top_bid_low = _parse_optional_decimal(_get(cols, col_map.get("top_bid_low")))
        top_bid_high = _parse_optional_decimal(_get(cols, col_map.get("top_bid_high")))

        keywords.append(
            ParsedKeyword(
                keyword=keyword,
                source_file=filename,
                source_row=row_number,
                monthly_volume=monthly_volume,
                trend_3m=trend_3m,
                trend_12m=trend_12m,
                competition_score=competition_score,
                competition_index=competition_index,
                top_bid_low=top_bid_low,
                top_bid_high=top_bid_high,
            )
        )

    return GoogleAdsParseResult(
        meta=ParserMeta(
            parser_type="google_ads",
            encoding=encoding,
            header_row=header_index + 1,
            detected_separator=_separator_name(separator),
            matched_signals=matched_signals,
            source_format_valid=True,
        ),
        keywords=keywords,
        junk_rows=junk_rows,
    )


def _decode_content(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\xff\xfe"):
        return content.decode("utf-16-le"), "utf-16-le"
    if content.startswith(b"\xfe\xff"):
        return content.decode("utf-16-be"), "utf-16-be"
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig"), "utf-8-sig"
    sample = content[:200]
    if sample:
        even_nulls = sample[0::2].count(0)
        odd_nulls = sample[1::2].count(0)
        if odd_nulls > len(sample) // 4 and even_nulls < odd_nulls // 2:
            return content.decode("utf-16-le"), "utf-16-le"
        if even_nulls > len(sample) // 4 and odd_nulls < even_nulls // 2:
            return content.decode("utf-16-be"), "utf-16-be"
    try:
        return content.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return content.decode("utf-16"), "utf-16"


def _find_header(lines: list[str]) -> tuple[int | None, str | None, list[str] | None, int]:
    search_lines = lines[: min(10, len(lines))]
    best: tuple[int | None, str | None, list[str] | None, int] = (None, None, None, 0)
    for idx, line in enumerate(search_lines):
        sep = _detect_separator([line])
        headers = [_normalize_header(col) for col in _split_line(line, sep)]
        joined = " ".join(headers)
        matched = sum(1 for signal in HEADER_SIGNALS if signal in joined)
        if matched > best[3]:
            best = (idx, sep, headers, matched)
        if matched >= 2:
            return best
    if best[3] >= 2:
        return best
    return (None, None, None, 0)


def _detect_separator(sample_lines: list[str]) -> str:
    for sep in ("\t", ";", ","):
        counts = [len(_split_line(line, sep)) for line in sample_lines]
        if counts and max(counts) >= 4:
            return sep
    return ","


def _split_line(line: str, sep: str) -> list[str]:
    return next(csv.reader(io.StringIO(line), delimiter=sep), [])


def _normalize_header(value: str) -> str:
    return value.strip().strip('"').lower()


def _build_column_map(headers: list[str]) -> dict[str, int]:
    col_map: dict[str, int] = {}
    for idx, col in enumerate(headers):
        normalized = _fold_turkish(col)
        if "keyword" in normalized or "anahtar kelime" in normalized:
            if "negative" not in normalized:
                col_map.setdefault("keyword", idx)
        if "avg" in normalized and "search" in normalized:
            col_map["monthly_volume"] = idx
        if "ort. aylik aramalar" in normalized or "aylik aramalar" in normalized:
            col_map["monthly_volume"] = idx
        if "three month" in normalized or "uc aylik" in normalized:
            col_map["trend_3m"] = idx
        if "yoy" in normalized or "year over year" in normalized or "yildan yila" in normalized:
            col_map["trend_12m"] = idx
        if normalized in {"competition", "rekabet"}:
            col_map["competition_label"] = idx
        if ("competition" in normalized and "indexed" in normalized) or (
            "rekabet" in normalized and ("dizinli" in normalized or "endeks" in normalized)
        ):
            col_map["competition_index"] = idx
        if "top of page bid" in normalized and "low" in normalized:
            col_map["top_bid_low"] = idx
        if "top of page bid" in normalized and "high" in normalized:
            col_map["top_bid_high"] = idx
    return col_map


def _fold_turkish(value: str) -> str:
    return (
        value.replace("ı", "i")
        .replace("İ", "i")
        .replace("ğ", "g")
        .replace("Ğ", "g")
        .replace("ü", "u")
        .replace("Ü", "u")
        .replace("ş", "s")
        .replace("Ş", "s")
        .replace("ö", "o")
        .replace("Ö", "o")
        .replace("ç", "c")
        .replace("Ç", "c")
        .lower()
    )


def _get(cols: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(cols):
        return ""
    return cols[idx].strip()


def _is_junk_keyword(keyword: str) -> bool:
    stripped = keyword.strip().strip('"')
    if not stripped:
        return True
    if all(ch.isdigit() or ch in ".,%- " for ch in stripped):
        return True
    return any(pattern.search(stripped) for pattern in JUNK_PATTERNS)


def _parse_int(value: str, default: int = 0) -> int:
    parsed = _parse_optional_int(value)
    return default if parsed is None else parsed


def _parse_optional_int(value: str) -> int | None:
    number = _clean_numeric(value, keep_decimal=False)
    if not number:
        return None
    try:
        return int(number)
    except ValueError:
        return None


def _parse_decimal(value: str, default: Decimal = Decimal("0")) -> Decimal:
    parsed = _parse_optional_decimal(value)
    if parsed is None:
        return default
    if abs(parsed) > Decimal("9999"):
        return default
    return parsed


def _parse_optional_decimal(value: str) -> Decimal | None:
    cleaned = _clean_numeric(value, keep_decimal=True)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _clean_numeric(value: str, *, keep_decimal: bool) -> str:
    if not value:
        return ""
    raw = value.strip().strip('"').replace("\u00a0", " ")
    if "∞" in raw or "inf" in raw.lower():
        return ""
    raw = raw.replace("%", "")
    if keep_decimal:
        raw = _normalize_decimal_separator(raw)
        return re.sub(r"[^0-9.\-]", "", raw)
    return re.sub(r"[^0-9\-]", "", raw)


def _normalize_decimal_separator(value: str) -> str:
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            return value.replace(".", "").replace(",", ".")
        return value.replace(",", "")
    if "," in value:
        return value.replace(",", ".")
    return value


def _competition_score(index_value: int | None, label: str) -> Decimal:
    if index_value is not None:
        bounded = max(0, min(100, index_value))
        return Decimal(str(bounded / 100)).quantize(Decimal("0.01"))

    normalized = _fold_turkish(label.strip().strip('"'))
    if normalized in {"yuksek", "high"}:
        return Decimal("0.80")
    if normalized in {"orta", "medium"}:
        return Decimal("0.50")
    if normalized in {"dusuk", "low"}:
        return Decimal("0.20")

    numeric = _parse_optional_decimal(label)
    if numeric is not None:
        if numeric > 1:
            numeric = numeric / Decimal("100")
        return min(Decimal("1"), max(Decimal("0.01"), numeric)).quantize(Decimal("0.01"))

    return Decimal("0.50")


def _separator_name(separator: str) -> str:
    if separator == "\t":
        return "tab"
    if separator == ";":
        return "semicolon"
    return "comma"
