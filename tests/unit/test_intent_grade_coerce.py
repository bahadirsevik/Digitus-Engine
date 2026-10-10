"""G_T/G_A derece parse toleransı (Gemini JSON drift)."""
from app.core.channel.intent_analyzer import _coerce_grade


def test_coerce_grade_accepts_int_and_numeric_strings():
    assert _coerce_grade(1) is True
    assert _coerce_grade(0) is False
    assert _coerce_grade("1") is True
    assert _coerce_grade("0") is False
    assert _coerce_grade(1.0) is True


def test_coerce_grade_accepts_bool_and_word_strings():
    assert _coerce_grade(True) is True
    assert _coerce_grade(False) is False
    assert _coerce_grade("true") is True
    assert _coerce_grade("Yes") is True
    assert _coerce_grade("false") is False
    assert _coerce_grade("no") is False


def test_coerce_grade_unparseable_returns_none():
    assert _coerce_grade(None) is None
    assert _coerce_grade("belki") is None
    assert _coerce_grade({}) is None
    assert _coerce_grade([1]) is None
