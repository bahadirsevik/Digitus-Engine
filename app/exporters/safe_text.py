"""Export metin guvenligi (plan v4 — Codex tur-5/6).

Iki tehlike sinifi:
1. Excel formula injection: `=`, `+`, `-`, `@` ile baslayan hucre Excel'de
   formul olarak calisabilir (`=HYPERLINK(...)`, `+CMD`, `@SUM(...)`).
   Keyword/caption gibi disaridan gelen metinler hucrelere yazildigi icin
   TUM Excel metin yazimlari `safe_excel_text`'ten gecer.
2. ReportLab `Paragraph` mini-HTML yorumlar: `<yatirim>`, `A & B` gibi
   metinler PDF uretimini bozar. PDF govde yazimlari `safe_paragraph_text`.
"""
import re
from typing import Any
from xml.sax.saxutils import escape

# openpyxl'in reddettigi kontrol karakterleri (tab/newline haric C0 araligi)
_ILLEGAL_XLSX_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_excel_text(value: Any) -> Any:
    """Excel hucresine yazilacak degeri guvenli hale getirir.

    - SAYISAL (int/float/bool) ve None degerler AYNEN doner (metne cevrilmez).
    - Islem sirasi (tur-6 #4): once illegal/control karakter temizligi,
      SONRA bastaki whitespace'i hesaba katan formula-baslangic denetimi —
      `\\t=HYPERLINK(...)` gibi degerler kacamaz.
    - Tehlikeli baslangicta `'` oneki: Excel metni inert gosterir.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = _ILLEGAL_XLSX_CHARS.sub("", str(value))
    if text.lstrip().startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def safe_paragraph_text(value: Any) -> str:
    """ReportLab Paragraph icin metni escape eder.

    `<`, `>`, `&` escape edilir; satir sonlari kontrollu `<br/>` olur
    (Paragraph duz \\n'i yoksayar).
    """
    if value is None:
        return ""
    text = escape(str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
