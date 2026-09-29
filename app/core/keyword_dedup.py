"""
Turkish Keyword Deduplication Module.

Provides fuzzy matching with Turkish suffix stripping to identify
near-duplicate keywords during bulk import. Keywords are merged only
when both text similarity (≥85%) AND metrics (volume, competition) match.
"""
from typing import List, Dict, Any, Tuple
from loguru import logger

try:
    from rapidfuzz import fuzz
except ImportError:
    try:
        from thefuzz import fuzz
    except ImportError:
        # Graceful fallback — disable fuzzy matching if neither library installed
        fuzz = None
        logger.warning("rapidfuzz/thefuzz not installed — fuzzy keyword deduplication disabled")


# Common Turkish suffixes ordered longest-first so we strip the most specific first
TURKISH_SUFFIXES = [
    # Complex suffixes (4+ chars)
    "cılık", "cilik", "culuk", "cülük",
    "ları", "leri",
    "lık", "lik", "luk", "lük",
    # Case suffixes
    "nın", "nin", "nun", "nün",
    "dan", "den", "tan", "ten",
    # Possessive
    "sı", "si", "su", "sü",
    # Plural
    "ler", "lar",
    # Simple case / accusative
    "da", "de", "ta", "te",
    "ı", "i", "u", "ü",
]

# Minimum similarity ratio (0-100) for fuzzy matching
FUZZY_THRESHOLD = 85

# Turkish special chars → ASCII equivalents for normalization before fuzzy compare
_TR_CHAR_MAP = str.maketrans({
    'ğ': 'g', 'ı': 'i', 'ş': 's', 'ü': 'u', 'ç': 'c', 'ö': 'o',
    'Ğ': 'G', 'İ': 'I', 'Ş': 'S', 'Ü': 'U', 'Ç': 'C', 'Ö': 'O',
})


def normalize_turkish(text: str) -> str:
    """
    Normalize Turkish special characters to ASCII equivalents.
    
    ğ→g, ı→i, ş→s, ü→u, ç→c, ö→o
    
    This is critical for fuzzy matching because fuzz.ratio treats
    Turkish chars as entirely different characters, causing pairs like
    'gübre' vs 'gubre' to score only ~80% instead of 100%.
    """
    return text.translate(_TR_CHAR_MAP)


def strip_turkish_suffixes(text: str) -> str:
    """
    Lightweight Turkish suffix stripper.
    
    Strips common suffixes from the END of each word in the text.
    This is NOT a full morphological analyzer — it handles the
    most common inflectional suffixes that cause duplicate keywords.
    
    Args:
        text: Input keyword text (lowercase expected)
    
    Returns:
        Stemmed text with suffixes removed
    """
    words = text.lower().strip().split()
    stemmed = []
    
    for word in words:
        # Only strip from words longer than 3 characters to avoid
        # destroying short root words
        if len(word) > 3:
            for suffix in TURKISH_SUFFIXES:
                if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                    word = word[:-len(suffix)]
                    break  # Only strip one suffix per word
        stemmed.append(word)
    
    return " ".join(stemmed)


def are_metrics_equal(kw1: Dict[str, Any], kw2: Dict[str, Any]) -> bool:
    """
    Check if two keywords have the same volume and competition score.
    
    Args:
        kw1: First keyword dict (must have 'monthly_volume', 'competition_score')
        kw2: Second keyword dict
    
    Returns:
        True if metrics are equal
    """
    vol1 = kw1.get('monthly_volume', 0)
    vol2 = kw2.get('monthly_volume', 0)
    
    comp1 = float(kw1.get('competition_score', 0))
    comp2 = float(kw2.get('competition_score', 0))
    
    return vol1 == vol2 and abs(comp1 - comp2) < 0.001


def has_meaningful_metrics(kw: Dict[str, Any]) -> bool:
    """Default/sentinel metrics are not strong evidence for fuzzy equality."""
    try:
        volume = int(kw.get("monthly_volume", 0) or 0)
    except (TypeError, ValueError):
        volume = 0
    try:
        competition = float(kw.get("competition_score", 0.5) or 0.5)
    except (TypeError, ValueError):
        competition = 0.5
    return volume > 0 or abs(competition - 0.5) > 0.001


TOKEN_ALIGN_MIN = 60


def _tokens_aligned(stem_a: str, stem_b: str) -> bool:
    """Fuzzy merge için token-hizalama korumasi (Codex, Hissefy denetimi).

    Genel oran >=85 tek başına yetmez: 'en yuksek fiyatl hisse' ~
    'en dusuk fiyatl hisse' 88 verir ama patron birini pozitif digerini
    negatif isaretlemisti (zit niyetler tek kayda sikisiyordu; ayni bug
    'anlık hisse senedi' ~ 'yabancı hisse senedi' ciftinde de vardi).
    Kural: HER token, karsi tarafta >= TOKEN_ALIGN_MIN benzerlikte bir
    es bulmali (iki yonde de) — 'yuksek'~'dusuk' 55 ve 'an'~'yabanc' 50
    engellenir; mesru ek-varyantlari ('tavsiye'~'tavsiyesi' stem sonrasi
    100) etkilenmez. Kacan merge yanlis merge'den UCUZDUR (kelime ayri
    kalir), o yuzden esik bilincli temkinli.
    """
    if fuzz is None:  # pragma: no cover - fuzzy lib yoksa merge zaten yok
        return True
    tokens_a, tokens_b = stem_a.split(), stem_b.split()
    if not tokens_a or not tokens_b:
        return True
    for src, dst in ((tokens_a, tokens_b), (tokens_b, tokens_a)):
        for tok in src:
            if max(fuzz.ratio(tok, other) for other in dst) < TOKEN_ALIGN_MIN:
                return False
    return True


def deduplicate_keywords(
    keywords: List[Dict[str, Any]],
    return_merge_details: bool = False,
):
    """
    Remove fuzzy duplicates from a keyword list.

    Two keywords are considered duplicates when:
    1. Their stemmed forms have ≥85% fuzzy similarity, AND
    2. Their metrics (monthly_volume, competition_score) are identical.

    When duplicates are found, the FIRST occurrence is kept.

    Args:
        keywords: List of keyword dicts with at least 'keyword',
                  'monthly_volume', 'competition_score' keys
        return_merge_details: True → returns tuple (deduped, merged_details)
                              where merged_details = [{dropped, kept}, ...]

    Returns:
        Deduplicated keyword list, or (list, merged_details) if return_merge_details=True
    """
    if not keywords or len(keywords) <= 1:
        return (keywords, []) if return_merge_details else keywords

    if fuzz is None:
        logger.warning("fuzzy lib not available — skipping fuzzy dedup, using exact match only")
        result = _exact_dedup(keywords)
        return (result, []) if return_merge_details else result
    
    # Build normalized+stemmed representations for fuzzy comparison
    # Pipeline: lowercase → suffix strip → Turkish char normalize
    stemmed_forms = []
    normalized_forms = []
    for kw in keywords:
        text = kw.get('keyword', '').lower().strip()
        stemmed = strip_turkish_suffixes(text)
        normalized = normalize_turkish(stemmed)
        stemmed_forms.append(stemmed)
        normalized_forms.append(normalized)
    
    # Track which indices are duplicates (to be removed)
    duplicate_indices = set()
    merge_details: List[Dict[str, str]] = []
    total_merged = 0

    for i in range(len(keywords)):
        if i in duplicate_indices:
            continue

        for j in range(i + 1, len(keywords)):
            if j in duplicate_indices:
                continue

            # First check: exact match on normalized text
            if normalized_forms[i] == normalized_forms[j]:
                if are_metrics_equal(keywords[i], keywords[j]):
                    duplicate_indices.add(j)
                    total_merged += 1
                    merge_details.append({
                        'dropped': keywords[j].get('keyword', ''),
                        'kept': keywords[i].get('keyword', ''),
                    })
                continue

            # Second check: fuzzy similarity on normalized+stemmed forms
            ratio = fuzz.ratio(normalized_forms[i], normalized_forms[j])

            if ratio >= FUZZY_THRESHOLD:
                if (
                    has_meaningful_metrics(keywords[i])
                    and has_meaningful_metrics(keywords[j])
                    and are_metrics_equal(keywords[i], keywords[j])
                    # Codex (Hissefy denetimi): genel oran yüksek olsa da
                    # 'en yüksek fiyatlı hisseler' ~ 'en düşük fiyatlı
                    # hisseler' (88) gibi ZIT-NİYET çiftleri birleşiyordu
                    # — her token karşı tarafta eş bulmalı
                    and _tokens_aligned(normalized_forms[i],
                                        normalized_forms[j])
                ):
                    duplicate_indices.add(j)
                    total_merged += 1
                    merge_details.append({
                        'dropped': keywords[j].get('keyword', ''),
                        'kept': keywords[i].get('keyword', ''),
                    })

    if total_merged > 0:
        logger.info(f"Fuzzy deduplication: {total_merged} duplicate(s) merged from {len(keywords)} keywords")

    deduped = [kw for idx, kw in enumerate(keywords) if idx not in duplicate_indices]
    return (deduped, merge_details) if return_merge_details else deduped


def _exact_dedup(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback: exact text dedup only (when thefuzz is unavailable)."""
    seen = {}
    result = []
    
    for kw in keywords:
        text = kw.get('keyword', '').lower().strip()
        if text in seen:
            # Check metrics before skipping
            if are_metrics_equal(seen[text], kw):
                continue
        seen[text] = kw
        result.append(kw)
    
    return result
