"""
Keyword normalizasyon — tek source of truth.

Türkçe karakter mapping + Unicode NFKD + whitespace normalize.
Python "İ".lower() edge-case için doğru sıra: TRANSLATE ÖNCE, lower SONRA.
"""
import re
import unicodedata
from functools import lru_cache

_TURKISH_MAP = str.maketrans({
    'ç': 'c', 'Ç': 'c',
    'ğ': 'g', 'Ğ': 'g',
    'ı': 'i', 'I': 'i',
    'İ': 'i', 'i': 'i',
    'ö': 'o', 'Ö': 'o',
    'ş': 's', 'Ş': 's',
    'ü': 'u', 'Ü': 'u',
})


@lru_cache(maxsize=4096)
def _keeps_marks(ch: str) -> bool:
    """Taban harf Latin-dışı bir alfabeye aitse (Kiril/Yunan/...) üstündeki işaret anlam taşır."""
    return not ch.isascii() and ch.isalpha() and not unicodedata.name(ch, '').startswith('LATIN')


def normalize_keyword(text: str) -> str:
    """
    'İSTANBUL'    → 'istanbul'
    'FÖN TARAĞI'  → 'fon taragi'
    'tıraş'       → 'tiras'

    Sıra:
      1. Türkçe karakter mapping (büyük/küçük her türlü)
      2. .lower()
      3. Unicode NFKD normalize (combining mark'ları ayır)
      4. Combining mark'ları yalnız Latin (ve harf-olmayan) tabandan sil; Kiril/Yunan
         tabanda koru ve NFC ile yeniden birleştir ('й'≠'и', 'ё'≠'е', 'ї'≠'і';
         NFC ve NFD girdi aynı anahtarı verir)
      5. Whitespace normalize
    """
    if not text:
        return ''

    s = text.translate(_TURKISH_MAP)          # 1. Türkçe map ÖNCE
    s = s.lower().strip()                     # 2. lower SONRA
    s = unicodedata.normalize('NFKD', s)      # 3. Combining mark cleanup
    out = []
    keep = False
    for ch in s:
        if unicodedata.combining(ch):
            if keep:
                out.append(ch)
            continue
        keep = _keeps_marks(ch)
        out.append(ch)
    s = unicodedata.normalize('NFC', ''.join(out))
    s = re.sub(r'\s+', ' ', s)               # 4. Whitespace normalize
    return s