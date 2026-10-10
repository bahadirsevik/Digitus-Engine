import pytest

from app.core.keyword_junk import junk_reason


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", "empty"),
        ("   ", "empty"),
        (None, "empty"),
        ('""', "empty"),
        ('"  "', "empty"),
        ("!!!", "symbol_only"),
        ("—", "symbol_only"),
        ("%%", "symbol_only"),
        ("123", "numeric_only"),
        ("12.5", "numeric_only"),
        ("1,000", "numeric_only"),
        ("50%", "numeric_only"),
        ("8681234567890", "numeric_only"),
        ("1 000 000", "numeric_only"),
        ("+90", "numeric_only"),
        ("1/2", "numeric_only"),
        ("-5", "numeric_only"),
    ],
)
def test_junk_reason_flags_junk(text, expected):
    assert junk_reason(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "8681234567890 sensodyne",
        "iphone 15 pro 256",
        "2.el telefon",
        "ü",
        "a",
        "x1",
        "ölçü 5%",
        "ş",
        "12#3",
    ],
)
def test_junk_reason_keeps_real_queries(text):
    assert junk_reason(text) is None
