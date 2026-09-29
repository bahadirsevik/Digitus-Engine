"""
Turkish text normalizer for embedding pre-processing.
Handles Turkish-specific character issues and common typo patterns.
"""
import re
import unicodedata

# Embedding cache anahtarının parçası (plan E): normalize_turkish'in
# davranışı değişirse bu sayı artırılmalı — eski cache girdileri aynı
# ham metin için farklı normalize sonuç taşıyabilir, sessiz karışmamalı.
NORMALIZER_VERSION = 1

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

    # Büyük `İ` lower()'dan ÖNCE katlanır: Python'da 'İ'.lower() -> 'i' + U+0307
    # (birleşik nokta) üretir ve noktalama temizliği bunu boşluğa çevirip
    # `İstanbul`u `i stanbul` yapıyordu (plan_v3_lokasyon_filtresi §12).
    # Başka yerde zaten lower() edilmiş girdideki `i̇` de sadeleştirilir.
    # NORMALIZER_VERSION BİLİNÇLİ olarak artırılmadı: embedding cache anahtarı
    # normalize metnin md5'ini taşır; `İ` içermeyen metinlerin çıktısı ve
    # anahtarı değişmez, `İ` içerenler doğal olarak yeni anahtara düşer.
    text = text.replace("İ", "i").lower().replace("i̇", "i").strip()

    # Replace Turkish special chars with ASCII equivalents
    for tr_char, ascii_char in TURKISH_CHAR_MAP.items():
        text = text.replace(tr_char, ascii_char)

    # Remove punctuation except hyphens and spaces
    text = re.sub(r"[^\w\s-]", " ", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text
