"""
Google Ads RSA Validators.

Contains:
- HeadlineValidator: 3-phase character limit fixing
- DescriptionValidator: Sentence-trim then truncate
- DKIValidator: Regex-based DKI format validation
"""
import re
from typing import Optional, Tuple
from loguru import logger

from app.generators.ads.prompt_templates import (
    HEADLINE_REGENERATION_PROMPT,
    DESCRIPTION_SHORTENING_PROMPT,
)


class HeadlineValidator:
    """
    3-phase headline validation.
    
    Phase 1: Rule-based shortening (e.g., " ve " → " & ")
    Phase 2: AI regeneration (optional, costly but quality)
    Phase 3: Elimination (if nothing works)
    """
    
    MAX_LENGTH = 30
    
    # Common shortening rules for Turkish
    SHORTENING_RULES = {
        " ve ": " & ",
        " için ": " ",
        " ile ": " - ",
        "Ürünleri": "",
        "Hizmetleri": "",
        "Kampanyası": "",
        " İndirimli": "",
        " Fırsatları": "",
        "Modelleri": "",
        "Çeşitleri": "",
        " Türkiye": "",
    }
    
    def validate(
        self, 
        headline: str, 
        keyword: str,
        ai_service = None,
        enable_regeneration: bool = True
    ) -> Tuple[Optional[str], str, Optional[str]]:
        """
        Validate and fix headline character limit.
        
        Args:
            headline: Original headline text
            keyword: Related keyword for context
            ai_service: AI service for regeneration (optional)
            enable_regeneration: Whether to use AI for fixing
            
        Returns:
            Tuple of (validated_text, action, reason)
            - action: "kept", "shortened", "regenerated", "eliminated"
            - reason: Explanation of what happened
        """
        original_len = len(headline)
        
        # Already valid
        if original_len <= self.MAX_LENGTH:
            logger.debug(f"Headline kept: '{headline}' ({original_len} chars)")
            return (headline, "kept", None)
        
        # Phase 1: Rule-based shortening
        shortened = self._apply_shortening_rules(headline)
        if len(shortened) <= self.MAX_LENGTH:
            logger.info(f"Headline shortened: {original_len} → {len(shortened)} chars")
            return (shortened, "shortened", f"{original_len} → {len(shortened)} chars via rules")
        
        # Phase 2: AI Regeneration
        if ai_service and enable_regeneration:
            regenerated = self._regenerate_with_ai(headline, keyword, ai_service, original_len)
            if regenerated and len(regenerated) <= self.MAX_LENGTH:
                logger.info(f"Headline regenerated: {original_len} → {len(regenerated)} chars via AI")
                return (regenerated, "regenerated", f"AI generated {len(regenerated)} chars")
            else:
                logger.warning(f"AI regeneration failed for: '{headline[:30]}...'")
        
        # Phase 3: Elimination
        logger.warning(f"Headline eliminated ({original_len} chars): '{headline[:40]}...'")
        return (None, "eliminated", f"{original_len} chars, could not fix")
    
    def _apply_shortening_rules(self, text: str) -> str:
        """Apply rule-based shortening."""
        result = text
        for old, new in self.SHORTENING_RULES.items():
            result = result.replace(old, new)
        return result.strip()
    
    def _regenerate_with_ai(
        self, 
        headline: str, 
        keyword: str, 
        ai_service,
        original_length: int
    ) -> Optional[str]:
        """Use AI to regenerate headline within limits."""
        try:
            prompt = HEADLINE_REGENERATION_PROMPT.format(
                original=headline,
                keyword=keyword,
                char_count=original_length
            )
            result = ai_service.complete(prompt, max_tokens=50).strip()
            
            # Clean quotes
            result = result.strip('"\'')
            
            # Validate result
            if result and len(result) <= self.MAX_LENGTH:
                return result
            return None
            
        except Exception as e:
            logger.warning(f"AI regeneration error: {e}")
            return None


class DescriptionValidator:
    """
    Description validation with sentence-aware + AI shortening.
    
    Strategy:
    1. If within limit, keep as-is
    2. Try to cut at sentence boundary (., !, ?)
    3. Try AI shortening (optional)
    4. Fall back to word-boundary truncate + "..."
    """
    
    MAX_LENGTH = 90
    MIN_SENTENCE_LENGTH = 50  # Minimum chars before we look for sentence end
    AI_MAX_TOKENS = 80
    
    def validate(
        self,
        description: str,
        ai_service=None,
        enable_ai_shortening: bool = True
    ) -> Tuple[str, str, Optional[str]]:
        """
        Validate and fix description character limit.
        
        Returns:
            Tuple of (validated_text, action, reason)
            - action: "kept", "sentence_trim", "ai_shortened", "truncated"
        """
        original_len = len(description)
        
        # Already valid
        if original_len <= self.MAX_LENGTH:
            return (description, "kept", None)
        
        # Phase 1: Try sentence-aware trimming
        cut_off = description[:self.MAX_LENGTH]
        
        # Find last sentence boundary within limit
        last_period = cut_off.rfind('.')
        last_exclaim = cut_off.rfind('!')
        last_question = cut_off.rfind('?')
        
        last_punct = max(last_period, last_exclaim, last_question)
        
        # If we have a reasonable sentence (at least MIN_SENTENCE_LENGTH chars)
        if last_punct >= self.MIN_SENTENCE_LENGTH:
            trimmed = description[:last_punct + 1]
            logger.info(f"Description sentence-trimmed: {original_len} → {len(trimmed)} chars")
            return (trimmed, "sentence_trim", f"Cut at punctuation, {len(trimmed)} chars")

        # Phase 2: AI shortening (plain-text)
        if ai_service and enable_ai_shortening:
            shortened = self._shorten_with_ai(description, ai_service, original_len)
            if shortened:
                logger.info(f"Description AI-shortened: {original_len} → {len(shortened)} chars")
                return (shortened, "ai_shortened", f"AI shortening: {original_len} → {len(shortened)} chars")

            truncated = self._truncate_with_ellipsis(description)
            logger.info(f"Description truncated after AI failure: {original_len} → {len(truncated)} chars")
            return (truncated, "truncated", "AI shortening failed, word-boundary fallback")

        # Phase 3: Word-boundary truncate with ellipsis
        truncated = self._truncate_with_ellipsis(description)
        logger.info(f"Description truncated: {original_len} → {len(truncated)} chars")
        return (truncated, "truncated", "Word-boundary cut + ellipsis")

    def _shorten_with_ai(
        self,
        description: str,
        ai_service,
        original_length: int
    ) -> Optional[str]:
        """Use AI to shorten description within limits."""
        try:
            prompt = DESCRIPTION_SHORTENING_PROMPT.format(
                original=description,
                char_count=original_length
            )
            result = ai_service.complete(prompt, max_tokens=self.AI_MAX_TOKENS).strip()

            # Clean wrapping quotes and normalize whitespace
            result = result.strip('"\'')
            result = re.sub(r"\s+", " ", result).strip()

            # Validate result quality + length
            if not result:
                return None
            if len(result) > self.MAX_LENGTH:
                return None
            if len(result.split()) < 2:
                return None
            return result
        except Exception as e:
            logger.warning(f"Description AI shortening error: {e}")
            return None

    def _truncate_with_ellipsis(self, description: str) -> str:
        """Word-boundary truncate with ellipsis as the last-resort fallback."""
        truncate_at = self.MAX_LENGTH - 3  # Leave room for "..."
        last_space = description[:truncate_at].rfind(' ')
        if last_space > 0:
            return description[:last_space] + "..."
        return description[:truncate_at] + "..."


class DKIValidator:
    """
    Dynamic Keyword Insertion format validation.
    
    Valid format: {KeyWord:Default Text} or {Keyword:Default}
    Invalid formats are converted to plain text using the default value.
    """
    
    # Valid DKI pattern: {KeyWord:DefaultText}
    VALID_PATTERN = r'\{(KeyWord|Keyword):([^}]+)\}'
    
    # Invalid patterns to detect
    INVALID_PATTERNS = [
        (r'\{(KeyWord|Keyword|keyword)\}', "Missing default value"),
        (r'\{:[^}]+\}', "Missing keyword token"),
        (r'\{[^:{}]+\}', "Missing colon"),
        (r'\{[^}]*:[^}]*:[^}]*\}', "Multiple colons"),
        (r'\{\s*(KeyWord|Keyword):\s+[^}]*\}', "Space after colon"),
        (r'\{(keyword):[^}]+\}', "Lowercase keyword token"),  # Should be KeyWord or Keyword
    ]
    
    def validate(self, headline: str) -> Tuple[str, bool, Optional[str]]:
        """
        Validate DKI format in headline.
        
        Returns:
            Tuple of (validated_text, is_valid_dki, conversion_reason)
            - is_valid_dki: True if headline contains valid DKI
            - conversion_reason: If DKI was converted to plain text, why
        """
        # No DKI in headline
        if '{' not in headline:
            return (headline, False, None)
        
        # Check for invalid patterns
        for pattern, reason in self.INVALID_PATTERNS:
            if re.search(pattern, headline, re.IGNORECASE if 'keyword' in pattern.lower() else 0):
                fixed = self._convert_to_plain(headline)
                logger.warning(f"Invalid DKI fixed ({reason}): '{headline}' → '{fixed}'")
                return (fixed, False, reason)
        
        # Check for valid DKI
        if re.search(self.VALID_PATTERN, headline):
            logger.debug(f"Valid DKI found: '{headline}'")
            return (headline, True, None)
        
        # Unknown curly braces - clean them
        fixed = self._convert_to_plain(headline)
        logger.warning(f"Unknown braces cleaned: '{headline}' → '{fixed}'")
        return (fixed, False, "Unknown brace format cleaned")
    
    def _convert_to_plain(self, headline: str) -> str:
        """
        Convert DKI to plain text using default value.
        {KeyWord:Laptoplar} → Laptoplar
        """
        # Replace valid DKI with default value
        result = re.sub(self.VALID_PATTERN, r'\2', headline)
        
        # Clean any remaining curly braces
        result = re.sub(r'[{}]', '', result)
        
        return result.strip()
    
    def extract_dki_default(self, headline: str) -> Optional[str]:
        """Extract the default value from a DKI headline."""
        match = re.search(self.VALID_PATTERN, headline)
        if match:
            return match.group(2)
        return None
