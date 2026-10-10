"""content_disposition yardimcisi: latin-1 disi ad, tirnak, satir sonu."""

import re
from urllib.parse import unquote

from app.core.http_headers import content_disposition


def _parts(header: str):
    m = re.fullmatch(
        r'attachment; filename="([^"]*)"; filename\*=UTF-8\'\'([^;]+)', header
    )
    assert m, header
    return m.group(1), unquote(m.group(2))


def test_turkish_name_folds_ascii_and_keeps_utf8():
    header = content_disposition("Şişli İçerik ğüöç.xlsx")
    header.encode("latin-1")  # Starlette bunu yapar; patlamamali
    ascii_part, utf8_part = _parts(header)
    assert ascii_part == "Sisli Icerik guoc.xlsx"
    assert utf8_part == "Şişli İçerik ğüöç.xlsx"


def test_quotes_and_backslash_stripped_from_ascii():
    header = content_disposition('a"b\\c.xlsx')
    ascii_part, utf8_part = _parts(header)
    assert '"' not in ascii_part and "\\" not in ascii_part
    assert ascii_part == "abc.xlsx"
    assert utf8_part == 'a"b\\c.xlsx'


def test_newline_injection_neutralised():
    header = content_disposition("x\r\nSet-Cookie: a=b.xlsx")
    assert "\r" not in header and "\n" not in header
    assert "%0D" not in header and "%0A" not in header


def test_pure_ascii_unchanged():
    ascii_part, utf8_part = _parts(content_disposition("report_1.xlsx"))
    assert ascii_part == "report_1.xlsx"
    assert utf8_part == "report_1.xlsx"


def test_empty_name_uses_fallback_with_extension():
    assert _parts(content_disposition(""))[0] == "download"
    assert _parts(content_disposition("ışık.xlsx"))[0] == "isik.xlsx"
    assert _parts(content_disposition("你好.xlsx"))[0] == "download.xlsx"


def test_inline_disposition():
    assert content_disposition("a.pdf", "inline").startswith("inline; ")
