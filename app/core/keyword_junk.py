"""Ortak cop kelime filtresi (plan_yapilacaklar.md 3.3, DAR kapsam).

Tek kaynak: CSV ayristirici, `crud.create_keywords_bulk`, `build_import_plan`
ve workspace yenilemesi ayni karari verir.

Kapsam BILINCLI olarak yalnizca uc durumdur:
  - 'empty'        : bosluk/tirnak soyulunca bos metin
  - 'symbol_only'  : ne harf ne rakam iceren metin ("!!!", "—", "%%")
  - 'numeric_only' : rakam iceren, harf icermeyen, rakam disi her karakteri
                     bosluk veya `. , % - + /` olan metin ("123", "12.5", "50%")

En az bir HARF iceren hicbir sey cop degildir. GTIN/barkod veya model numarasi
yanina metin gelen aramalar ("8681234567890 sensodyne", "iphone 15 pro 256")
KALIR; uzun sayi token'i kurali YOKTUR.

`+` ve `/` numeric_only'ye bilincli eklendi ("+90", "1/2" gibi harfsiz ifadeler
anlamli bir arama degildir); mevcut parser kurali (rakam + `.,%-` + bosluk) bunun
alt kumesidir.
"""
from __future__ import annotations

from typing import Optional

_NUMERIC_EXTRA = frozenset(".,%-+/")
_QUOTES = "\"'“”‘’"


def junk_reason(text: Optional[str]) -> Optional[str]:
    """'empty' | 'symbol_only' | 'numeric_only' ya da None (cop degil)."""
    stripped = (text or "").strip().strip(_QUOTES).strip()
    if not stripped:
        return "empty"
    has_digit = False
    for ch in stripped:
        if ch.isalpha():
            return None
        if ch.isdigit():
            has_digit = True
    if not has_digit:
        return "symbol_only"
    if all(ch.isdigit() or ch.isspace() or ch in _NUMERIC_EXTRA for ch in stripped):
        return "numeric_only"
    # Rakam + baska semboller ("12#3"): kapsam disi, cop sayilmaz.
    return None
