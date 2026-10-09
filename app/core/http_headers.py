"""HTTP header yardimcilari.

Starlette header degerlerini latin-1 ile kodlar; kullanici kaynakli bir ad
("Şişli" vb.) elle kurulan ``Content-Disposition`` icinde UnicodeEncodeError
→ HTTP 500 uretir, tirnak/satir sonu ise header'i bozar. Tum indirme
uclari bu yardimciyi kullanmalidir.
"""

import os
import re
from urllib.parse import quote

_TR_FOLD = str.maketrans({
    "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
    "ö": "o", "Ö": "O", "ü": "u", "Ü": "U", "ç": "c", "Ç": "C",
})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(name: str) -> str:
    """Kontrol karakterlerini (satir sonu dahil) bosluga cevirir."""
    return _CONTROL_RE.sub(" ", name or "").strip()


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """``attachment; filename="<ascii>"; filename*=UTF-8''<pct-encoded>`` uretir."""
    cleaned = _clean(filename)
    ext = os.path.splitext(cleaned)[1]
    ascii_ext = (
        ext.translate(_TR_FOLD).encode("ascii", "ignore").decode("ascii")
        if ext else ""
    )
    ascii_ext = re.sub(r'["\\\s]', "", ascii_ext)

    fallback = cleaned.translate(_TR_FOLD).encode("ascii", "ignore").decode("ascii")
    fallback = fallback.replace('"', "").replace("\\", "").strip()
    if not fallback or fallback == ascii_ext:
        fallback = f"download{ascii_ext}"

    encoded = quote(cleaned or fallback, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
