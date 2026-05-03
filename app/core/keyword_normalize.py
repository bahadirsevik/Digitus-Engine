"""
Keyword normalizasyon — tek source of truth.

Türkçe karakter mapping + Unicode NFKD + whitespace normalize.
Python "İ".lower() edge-case için doğru sıra: TRANSLATE ÖNCE, lower SONRA.
"""
import re
import unicodedata

_TURKISH_MAP = str.maketrans({
    'ç': 'c', 'Ç': 'c',
    'ğ': 'g', 'Ğ': 'g',
    'ı': 'i', 'I': 'i',
    'İ': 'i', 'i': 'i',
    'ö': 'o', 'Ö': 'o',
    'ş': 's', 'Ş': 's',
    'ü': 'u', 'Ü': 'u',
})


def normalize_keyword(text: str) -> str:
    """
    'İSTANBUL'    → 'istanbul'
    'FÖN TARAĞI'  → 'fon taragi'
    'tıraş'       → 'tiras'

    Sıra:
      1. Türkçe karakter mapping (büyük/küçük her türlü)
      2. .lower()
      3. Unicode NFKD normalize (combining mark'ları ayır)
      4. Combining mark'ları sil
      5. Whitespace normalize
    """
    if not text:
        return ''

    s = text.translate(_TURKISH_MAP)          # 1. Türkçe map ÖNCE
    s = s.lower().strip()                     # 2. lower SONRA
    s = unicodedata.normalize('NFKD', s)      # 3. Combining mark cleanup
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'\s+', ' ', s)               # 4. Whitespace normalize
    return s