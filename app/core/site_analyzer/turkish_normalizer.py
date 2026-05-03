"""
Turkish text normalizer for embedding pre-processing.
Handles Turkish-specific character issues and common typo patterns.
"""
import re
import unicodedata

# Turkish special char mappings (ASCII variants → proper Turkish)
TURKISH_CHAR_MAP = {
    "ı": "i", "İ": "i",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ş": "s", "Ş": "s",
    "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
}


def normalize_turkish(text: str) -> str:
    """
    Normalize Turkish text for embedding similarity.
    - Lowercase
    - Normalize Turkish chars to ASCII equivalents
    - Remove extra whitespace
    - Strip punctuation
    """
    if not text:
        return ""

    text = text.lower().strip()

    # Replace Turkish special chars with ASCII equivalents
    for tr_char, ascii_char in TURKISH_CHAR_MAP.items():
        text = text.replace(tr_char, ascii_char)

    # Remove punctuation except hyphens and spaces
    text = re.sub(r"[^\w\s-]", " ", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text
