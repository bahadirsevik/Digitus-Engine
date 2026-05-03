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


def deduplicate_keywords(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove fuzzy duplicates from a keyword list.
    
    Two keywords are considered duplicates when:
    1. Their stemmed forms have ≥85% fuzzy similarity, AND
    2. Their metrics (monthly_volume, competition_score) are identical.
    
    When duplicates are found, the FIRST occurrence is kept.
    
    Args:
        keywords: List of keyword dicts with at least 'keyword', 
                  'monthly_volume', 'competition_score' keys
    
    Returns:
        Deduplicated keyword list
    """
    if not keywords or len(keywords) <= 1:
        return keywords
    
    if fuzz is None:
        logger.warning("fuzzy lib not available — skipping fuzzy dedup, using exact match only")
        return _exact_dedup(keywords)
    
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
    total_merged = 0
    
    for i in range(len(keywords)):
        if i in duplicate_indices:
            continue
        
        for j in range(i + 1, len(keywords)):
            if j in duplicate_indices:
                continue
            
            # First check: exact match on normalized text
            # This catches 'doğal gübre' == 'dogal gubre' without fuzzy overhead
            if normalized_forms[i] == normalized_forms[j]:
                if are_metrics_equal(keywords[i], keywords[j]):
                    duplicate_indices.add(j)
                    total_merged += 1
                    logger.debug(
                        f"Normalized exact match merged: "
                        f"'{keywords[j].get('keyword','')}' ≈ '{keywords[i].get('keyword','')}'"
                    )
                continue
            
            # Second check: fuzzy similarity on normalized+stemmed forms
            ratio = fuzz.ratio(normalized_forms[i], normalized_forms[j])
            
            if ratio >= FUZZY_THRESHOLD:
                # High text similarity — check metrics
                if are_metrics_equal(keywords[i], keywords[j]):
                    duplicate_indices.add(j)
                    total_merged += 1
                    logger.debug(
                        f"Fuzzy duplicate merged: '{keywords[j].get('keyword','')}' ≈ '{keywords[i].get('keyword','')}' "
                        f"(ratio={ratio}%, same metrics)"
                    )
                else:
                    logger.debug(
                        f"Fuzzy similar but different metrics, kept separate: "
                        f"'{keywords[j].get('keyword','')}' ≈ '{keywords[i].get('keyword','')}' (ratio={ratio}%)"
                    )
    
    if total_merged > 0:
        logger.info(f"Fuzzy deduplication: {total_merged} duplicate(s) merged from {len(keywords)} keywords")
    
    # Return non-duplicate keywords preserving order
    return [kw for idx, kw in enumerate(keywords) if idx not in duplicate_indices]


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
