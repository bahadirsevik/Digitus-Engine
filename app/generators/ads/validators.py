"""
Google Ads RSA Validators.

Contains:
- HeadlineValidator: 3-phase character limit fixing
- DescriptionValidator: Sentence-trim then truncate
- DKIValidator: Regex-based DKI format validation
"""
import re
from typing import Dict, List, Optional, Tuple, Union
from loguru import logger

from app.core.constants import (
    ADS_DESCRIPTIONS_PER_GROUP,
    ADS_HEADLINES_PER_GROUP,
    ADS_KEYWORDS_PER_GROUP,
    ADS_MIN_CTA_HEADLINES,
)
from app.core.site_analyzer.turkish_normalizer import normalize_turkish
from app.generators.ai_service import scoped
from app.generators.claim_lexicon import (
    ABSOLUTE_RX,
    CERT_PATTERN,
    SERVICE_RX,
    GroundingSource,
)
from app.generators.output_language import with_output_block
from app.schemas.ads import AdGroupStandardReport
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
        enable_regeneration: bool = True,
        output_ctx=None,
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
            regenerated = self._regenerate_with_ai(headline, keyword, ai_service, original_len, output_ctx)
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
        # Silinen kelime boşluk bırakır: çift boşluk ve DKI'de "}" önü ("{KeyWord:Sosyal Medya }")
        result = re.sub(r"\s+\}", "}", re.sub(r"\s{2,}", " ", result))
        return result.strip()
    
    def _regenerate_with_ai(
        self, 
        headline: str, 
        keyword: str, 
        ai_service,
        original_length: int,
        output_ctx=None,
    ) -> Optional[str]:
        """Use AI to regenerate headline within limits."""
        try:
            prompt = with_output_block(HEADLINE_REGENERATION_PROMPT.format(
                original=headline,
                keyword=keyword,
                char_count=original_length
            ), output_ctx)
            # Codex v8-7: mikro kısaltma düşünme gerektirmez —
            # Gemini 3.8 `minimal` kabul etmez; en dusuk desteklenen seviye `low`.
            result = scoped(
                ai_service, "micro_shorten", thinking_level="low"
            ).complete(prompt, max_tokens=800).strip()

            # Clean quotes
            result = result.strip('"\'')

            # Kesik/bozuk DKI parçasını kabul etme: düşünme kesintisi
            # '{KeyWord:Popüler Hiss' gibi dengesiz parantezli bir parça
            # döndürebilir — ≤30 karakter diye kabul edilirse bozuk başlık
            # yayına girer (set-7'de gerçekleşti). Dengesiz parantez = ret.
            if result.count('{') != result.count('}'):
                logger.warning(
                    f"AI regeneration rejected (unbalanced braces): '{result}'"
                )
                return None

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
    AI_MAX_TOKENS = 800
    
    def validate(
        self,
        description: str,
        ai_service=None,
        enable_ai_shortening: bool = True,
        output_ctx=None,
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
            shortened = self._shorten_with_ai(description, ai_service, original_len, output_ctx)
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
        original_length: int,
        output_ctx=None,
    ) -> Optional[str]:
        """Use AI to shorten description within limits."""
        try:
            prompt = with_output_block(DESCRIPTION_SHORTENING_PROMPT.format(
                original=description,
                char_count=original_length
            ), output_ctx)
            # Codex v8-7: mikro kısaltma düşünme gerektirmez (plan sözleşmesi)
            result = scoped(
                ai_service, "micro_shorten", thinking_level="low"
            ).complete(prompt, max_tokens=self.AI_MAX_TOKENS).strip()

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

    # Invalid patterns to detect — flag'ler desen basina ACIK verilir.
    # ONCEKI BUG: flag "if 'keyword' in pattern.lower()" kosuluyla IGNORECASE
    # yapiliyordu; 'Lowercase keyword token' deseni de IGNORECASE'e girip
    # GECERLI '{KeyWord:...}' baslıklarını yakalıyordu — hicbir dinamik
    # baslik uretimden sag cikamiyordu (run-15/16'da dki_converted'larin
    # tamami bu false-positive'di).
    INVALID_PATTERNS = [
        (r'\{(KeyWord|Keyword|keyword)\}', re.IGNORECASE, "Missing default value"),
        (r'\{:[^}]+\}', 0, "Missing keyword token"),
        (r'\{[^:{}]+\}', 0, "Missing colon"),
        (r'\{[^}]*:[^}]*:[^}]*\}', 0, "Multiple colons"),
        (r'\{\s*(KeyWord|Keyword|keyword)\s*:\s+[^}]*\}', re.IGNORECASE, "Space after colon"),
        # KUCUK harf 'keyword' token'i is kurali geregi gecersiz — bu desen
        # CASE-SENSITIVE kalmak ZORUNDA, yoksa KeyWord/Keyword'u da yakalar
        (r'\{keyword:[^}]+\}', 0, "Lowercase keyword token"),
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
        for pattern, flags, reason in self.INVALID_PATTERNS:
            if re.search(pattern, headline, flags):
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


# ==================== CLAIM GROUNDING VALIDATOR (Faz F) ====================
#
# Deterministik desteksiz-iddia tespiti. Kapsam AÇIKÇA sınırlı:
#   1) Sayısal iddialar: sayı + birim (+, %, puan, yıl, müşteri, yatırımcı,
#      kullanıcı ...) — "50.000", "50 bin", "yüzde 80", "4.8 puan" normalize
#      edilerek yakalanır.
#   2) Süperlatifler: "en iyi", "lider", "Türkiye'nin en ...", "1 numara".
#   3) Kazanç/getiri garantisi ifadeleri.
# "Güvenli ödeme / kolay iade / uzman / lisanslı" gibi SAYISAL OLMAYAN
# iddialar kapsam DIŞIDIR (deterministik tespit false-positive üretir);
# bunları prompt yasağı + few-shot temizliği taşır.
#
# grounding_facts = confirmed profil product_facts + kullanıcının İSTEKTE
# açıkça verdiği brand_usp (yalnız GÜVENİLİR kaynaklar — sistemin ürettiği
# generic fallback USP whitelist'e GİRMEZ).
#
# exempt_keywords: gruptaki TÜM target_keywords — asset içinde gerçekten
# geçen keyword parçası muaf (örn. keyword'deki "2026" doğal sayısı claim
# sayılmaz); aynı sayının keyword dışı bağlamda kullanımı muaf DEĞİLDİR.

_CLAIM_UNIT_TERMS = (
    "puan", "yıldız", "yildiz", "yıllık", "yillik", "yıldır", "yildir",
    "yıl", "yil", "müşteri", "musteri", "yatırımcı", "yatirimci",
    "kullanıcı", "kullanici", "kişi", "kisi", "üye", "uye", "yorum",
    "değerlendirme", "degerlendirme", "ülke", "ulke",
    # Sağlık müşterilerinin profil gerçekleri ("5000 hasta") RU/UK/EN/DE
    # "пациентов/patients/Patienten" iddiasını ground'layabilsin
    "hasta", "ameliyat", "operasyon",
)

# Birim eş anlamlıları — "10 yıl deneyim" fact'i "10 yıldır hizmet"
# iddiasını ground'lar (aynı kavram, farklı çekim)
_UNIT_CANON = {
    "yildir": "yıl", "yıldır": "yıl", "yil": "yıl", "yıllık": "yıl",
    "yillik": "yıl", "musteri": "müşteri", "yatirimci": "yatırımcı",
    "kullanici": "kullanıcı", "kisi": "kişi", "uye": "üye",
    "degerlendirme": "değerlendirme", "ulke": "ülke", "yildiz": "yıldız",
    "operasyon": "ameliyat",
}

# RU/UK/EN/DE birim tabloları: (regex parçası, kanonik birim). Kanonik değer
# Türkçe tablonun kanoniğidir: Türkçe profil gerçeği ("10 yıl", "5000 hasta")
# hedef dildeki "10 лет / 5000 пациентов / 10 years" iddiasını ground'lar.
# Desenler `ı`→`i` normalize edilmiş metinde çalışır.
_ML_UNIT_TERMS = {
    "ru": (
        (r"лет(?:н\w*)?\b|год(?:а|ов|ы|у)?\b", "yıl"),
        (r"клиент\w*|заказчик\w*|покупател\w*", "müşteri"),
        (r"пациент\w*", "hasta"),
        (r"операци\w*|процедур\w*|вмешательств\w*", "ameliyat"),
        (r"пользовател\w*", "kullanıcı"),
        (r"человек\b|люд(?:ей|и)\b", "kişi"),
        (r"отзыв\w*", "yorum"),
        (r"оценок\b|оценк\w*", "değerlendirme"),
        (r"звезд\w*|звёзд\w*", "yıldız"),
        (r"балл\w*", "puan"),
        (r"стран\w*", "ülke"),
        (r"инвестор\w*", "yatırımcı"),
    ),
    "uk": (
        (r"рок(?:ів|и|у)\b|рік\b|річн\w*", "yıl"),
        (r"клієнт\w*|покупц\w*", "müşteri"),
        (r"пацієнт\w*", "hasta"),
        (r"операці\w*|процедур\w*|втручан\w*", "ameliyat"),
        (r"користувач\w*", "kullanıcı"),
        (r"людей\b|люди\b", "kişi"),
        (r"відгук\w*", "yorum"),
        (r"оцінок\b|оцінк\w*", "değerlendirme"),
        (r"зір(?:ок|ки|ка)\b|зірк\w*", "yıldız"),
        (r"бал(?:ів|и|ла)?\b", "puan"),
        (r"країн\w*", "ülke"),
        (r"інвестор\w*", "yatırımcı"),
    ),
    "en": (
        (r"years?\b|yrs?\b", "yıl"),
        (r"customers?\b|clients?\b|buyers?\b", "müşteri"),
        (r"patients?\b", "hasta"),
        (r"procedures?\b|operations?\b|surger(?:y|ies)\b", "ameliyat"),
        (r"users?\b|subscribers?\b", "kullanıcı"),
        (r"members?\b", "üye"),
        (r"people\b|persons?\b", "kişi"),
        (r"reviews?\b", "yorum"),
        (r"ratings?\b", "değerlendirme"),
        (r"stars?\b", "yıldız"),
        (r"points?\b", "puan"),
        (r"countries\b|country\b", "ülke"),
        (r"investors?\b", "yatırımcı"),
    ),
    "de": (
        (r"jahr\w*", "yıl"),
        (r"kunden\b|kunde\b|käufer\b|kaeufer\b", "müşteri"),
        (r"klienten\b|patient(?:en|in|innen)?\b", "hasta"),
        (r"eingriff\w*|operation\w*|behandlung\w*", "ameliyat"),
        (r"nutzer\w*", "kullanıcı"),
        (r"mitglied\w*", "üye"),
        (r"personen\b|menschen\b", "kişi"),
        (r"rezension\w*", "yorum"),
        (r"bewertung\w*", "değerlendirme"),
        (r"stern\w*", "yıldız"),
        (r"punkt\w*", "puan"),
        (r"länder\b|laender\b", "ülke"),
        (r"anleger\b|investor\w*", "yatırımcı"),
    ),
}
_ML_UNITS_FLAT = tuple(
    (re.compile(pat), canon)
    for table in _ML_UNIT_TERMS.values()
    for pat, canon in table
)


def _canon_unit(unit: str) -> str:
    if unit in _UNIT_CANON:
        return _UNIT_CANON[unit]
    for rx, canon in _ML_UNITS_FLAT:
        if rx.fullmatch(unit):
            return canon
    return unit


_SUPERLATIVE_PATTERNS = (
    r"en\s+iyi",
    r"\blider\b",
    r"lideri\b",
    r"türkiye'?nin\s+en\s+\w+",
    r"turkiye'?nin\s+en\s+\w+",
    r"\b1\s*numara",
    r"\bbir\s+numara",
    r"\bno\.?\s*1\b",
)

# RU/UK/EN/DE süperlatifleri; `ı`→`i` normalize metinde çalışır.
_ML_SUPERLATIVE_PATTERNS = {
    "ru": (
        r"\bлучш\w*", r"\bнаилучш\w*", r"\bлидер\w*", r"\bведущ\w*",
        r"\bсам(?:ый|ая|ое|ые|ого|ому|ым|ых)\s+\w+",
        r"№\s*1\b", r"\bномер\s*(?:один|1)\b",
    ),
    "uk": (
        r"\bнай(?:кращ|більш|швидш|популярніш|надійніш|вигідніш|якісніш)\w*",
        r"\bлідер\w*", r"\bпровідн\w*",
        r"№\s*1\b", r"\bномер\s*(?:один|1)\b",
    ),
    "en": (
        r"\bbest\b", r"#\s*1\b", r"\bnumber\s*(?:one|1)\b", r"\bleading\b",
        r"\bleaders?\b", r"\btop[- ]rated\b",
        r"\bmost\s+(?:trusted|popular|reliable|advanced|affordable|experienced)\b",
        r"\bworld'?s\s+(?:best|leading|first|largest)\b",
        r"\bthe\s+(?:cheapest|fastest|biggest|largest)\b",
    ),
    "de": (
        r"\bbest(?:e|er|en|em|es)?\b", r"\bnr\.?\s*1\b",
        r"\bnummer\s*(?:eins|1)\b", r"\bf(?:ü|ue)hrend\w*",
        r"\bmarktf(?:ü|ue)hrer\w*",
        r"\bgr(?:ö|oe)(?:ß|ss)te[rnsm]?\b", r"\bbeliebteste[rnsm]?\b",
        r"\bg(?:ü|ue)nstigste[rnsm]?\b", r"\bschnellste[rnsm]?\b",
    ),
}

_GUARANTEE_PATTERNS = (
    r"kazan[çc]\s+garanti",
    r"garantili\s+kazan[çc]",
    r"k[âa]r\s+garanti",
    r"garantili\s+getiri",
    r"getiri\s+garanti",
    r"kesin\s+kazan[çc]",
)


def _guarantee_pair(guarantee: str, gain: str) -> tuple:
    """Garanti sözcüğü ile kazanç/sonuç sözcüğü en çok 2 sözcük arayla, iki sırada."""
    gap = r"(?:\W+\w+){0,2}?\W+"
    return (
        rf"\b(?:{guarantee})\b{gap}(?:{gain})\w*",
        rf"\b(?:{gain})\w*{gap}(?:{guarantee})\b",
    )


# Garanti = KAZANÇ/SONUÇ garantisi (TR tablosuyla aynı anlam). Salt "garanti /
# Garantie / warranty" (ürün garantisi, para iade garantisi) iddia SAYILMAZ;
# "гарантия результата", "guaranteed results", "garantierter Gewinn" yakalanır.
_ML_GUARANTEE_PATTERNS = {
    "ru": _guarantee_pair(
        r"гарант\w*",
        r"доход|прибыл|заработ|результат|успех|выигр|окупаемост|излечен|эффект",
    ),
    "uk": _guarantee_pair(
        r"гарант\w*",
        r"дохід|доход|прибут|заробіт|заробл|результат|успіх|виграш|окупн|вилікув|ефект",
    ),
    "en": _guarantee_pair(
        r"guarantee[sd]?|guaranteeing",
        r"income|profits?|earnings?|returns|results?|success|wins?|winnings|roi|cures?",
    ),
    "de": _guarantee_pair(
        r"garanti\w*",
        r"gewinn|einkommen|verdienst|rendite|ertr[äa]g|ergebnis|erfolg|heilung",
    ),
}

# Her dil için üç tablo da tanımlı olan kodlar (+ "tr" ana tablolar). Katalog
# dilleri (markets.LANGUAGES) bunun alt kümesi OLMAK ZORUNDA — test korur.
CLAIM_TERM_LANGUAGES = frozenset({"tr"}) | (
    set(_ML_UNIT_TERMS) & set(_ML_SUPERLATIVE_PATTERNS) & set(_ML_GUARANTEE_PATTERNS)
)
_ML_SUPERLATIVE_COMPILED = tuple(
    re.compile(p) for ps in _ML_SUPERLATIVE_PATTERNS.values() for p in ps
)
_ML_GUARANTEE_COMPILED = tuple(
    re.compile(p) for ps in _ML_GUARANTEE_PATTERNS.values() for p in ps
)


def _tr_lower(text: str) -> str:
    """Türkçe-güvenli lowercase (I→ı, İ→i)."""
    return text.replace("I", "ı").replace("İ", "i").lower()


# Çarpan sözcükleri (Türkçe + RU/UK/EN/DE); sayı gövdesi ve kanonikleştirme paylaşır
_MILLION_RX = r"milyon|миллион\w*|мільйон\w*|млн\.?|million\w*|mio\.?|mln"
_THOUSAND_RX = r"bin|тысяч\w*|тыс\.?|тисяч\w*|тис\.?|thousand|tausend"
_SPACE_GROUP = "[   ]"


def _canonical_number(raw: str) -> str:
    """Sayı biçimlerini tek biçime indirger: '50.000'/'50,000'/'50 bin'/'50 тыс.' → '50000'."""
    s = _tr_lower(raw).strip()
    multiplier = 1
    for rx, mult in ((_MILLION_RX, 1_000_000), (_THOUSAND_RX + r"|(?<=\d)k", 1_000)):
        m = re.search(rf"\s*(?:{rx})$", s)
        if m:
            s = s[:m.start()].strip()
            multiplier = mult
            break
    # Boşluklu binlik "10 000" → "10000"
    if re.fullmatch(rf"\d{{1,3}}(?:{_SPACE_GROUP}\d{{3}})+", s):
        s = re.sub(_SPACE_GROUP, "", s)
    # Ondalık "4.8" korunmalı; binlik "50.000" ayracı silinmeli.
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    else:
        s = s.replace(",", ".")
    try:
        value = float(s) * multiplier
    except ValueError:
        return re.sub(r"[^\d.]", "", s)
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


# Sayı gövdesi: 50 | 50.000 | 10 000 | 4,8 | 50 bin | 3 milyon | 50 тыс. | 10k.
# Başta lookbehind: sayının ortasından eşleşme başlamaz ("2026 450 müşteri").
_NUMBER_BODY = (
    rf"(?<!\d)(?<!\d[.,])(?:\d{{1,3}}(?:{_SPACE_GROUP}\d{{3}})+(?!\d)|\d[\d.,]*)"
    rf"(?:\s*(?:{_MILLION_RX}|{_THOUSAND_RX})|k\b)?"
)
_PERCENT_WORDS = r"%|percent\b|prozent\b|процент\w*|відсотк\w*"
_RATING_UNITS = (
    r"puan|yıldız|yildiz|stars?\b|sterne?\b|sternen\b|звезд\w*|звёзд\w*|зірок\b|"
    r"зір\w*|балл\w*|points?\b|punkte\b"
)


def _extract_numeric_claims(text: str) -> list:
    """Metindeki sayısal iddia adaylarını (span, canonical) döndürür."""
    lowered = _tr_lower(text)
    # ML birim terimleri 'i' ile yazılı; TR terimlerin ASCII ('yil') karşılıkları
    # tabloda var. ı→i 1:1 olduğundan ofsetler korunur, span orijinal metinden alınır.
    search = lowered.replace("ı", "i")
    claims = []

    def _txt(m):
        return lowered[m.start():m.end()].strip()

    # 4.8/5 (puan) rating biçimi — önce yakala ve metinden çıkar,
    # yoksa "/5 puan" parçası ayrı bir iddia gibi görünür.
    # FALSE-POSITIVE korumasi (idea-44 dersi): thread/carousel sayfalama
    # numaralari ("1/8", "2/8") rating DEGILDIR — yalnizca birim ("puan/
    # yildiz") VEYA ondalikli pay ("4.8/5") tasiyan N/M rating sayilir;
    # duz tam-sayi/tam-sayi oldugu gibi birakilir. Yerine boy korunarak boşluk
    # konur: sonraki eşleşmelerin ofsetleri kaymaz.
    def _consume_rating(m):
        num, unit = m.group(1), m.group(2)
        if unit or "." in num or "," in num:
            claims.append((_txt(m), _canonical_number(num) + " puan"))
            return " " * (m.end() - m.start())
        return m.group(0)  # sayfalama (1/8) — iddia degil

    search = re.sub(
        rf"(\d[\d.,]*)\s*/\s*\d+(\s*(?:{_RATING_UNITS}))?",
        _consume_rating,
        search,
    )

    # yüzde 80 / %80
    for m in re.finditer(rf"(?:%\s*|yüzde\s+|yuzde\s+)({_NUMBER_BODY})", search):
        claims.append((_txt(m), "%" + _canonical_number(m.group(1))))

    # 80% / 80 percent / 80 Prozent / 80 процентов
    for m in re.finditer(rf"({_NUMBER_BODY})\s*(?:{_PERCENT_WORDS})", search):
        claims.append((_txt(m), "%" + _canonical_number(m.group(1))))

    # 100.000+ (birimli veya birimsiz artı)
    for m in re.finditer(rf"({_NUMBER_BODY})\s*\+", search):
        claims.append((_txt(m), _canonical_number(m.group(1)) + "+"))

    # sayı + birim (10 yıl, 4.8 puan, 50 bin müşteri, 10 лет, 5 000 patients)
    unit_alt = "|".join(
        [*_CLAIM_UNIT_TERMS, *(p for t in _ML_UNIT_TERMS.values() for p, _ in t)]
    )
    for m in re.finditer(rf"({_NUMBER_BODY})(?:\s|-)*\+?(?:\s|-)*({unit_alt})", search):
        claims.append(
            (
                _txt(m),
                _canonical_number(m.group(1)) + " " + _canon_unit(m.group(2)),
            )
        )

    return claims


def _lexicon_violations(
    lowered: str, original: str, source: GroundingSource, include_profile_concepts: bool = True
) -> list:
    """Hizmet/marka, sertifika ve mutlak sonuç ihlalleri (plan_iddia_denetimi A1).

    lowered: _tr_lower'lı, MASKESİZ metin; original: sertifika için ham metin.
    """
    search = lowered.replace("ı", "i")
    units = source.permission_units()
    folded_units = [_tr_lower(u).replace("ı", "i") for u in units]
    violations = []

    if include_profile_concepts:
        for rx in SERVICE_RX.values():
            m = rx.search(search)
            if m and not any(rx.search(u) for u in folded_units):
                violations.append("service:" + search[m.start():m.end()])

        granted = {
            _cert_key(cm) for u in units for cm in CERT_PATTERN.finditer(u) if cm.group(2)
        }
        for cm in CERT_PATTERN.finditer(original):
            if not cm.group(2) or _cert_key(cm) not in granted:
                violations.append("cert:" + cm.group(0).strip())

    for rx in ABSOLUTE_RX:
        for m in rx.finditer(search):
            violations.append("absolute:" + search[m.start():m.end()])
    return violations


def _cert_key(m) -> tuple:
    org = m.group(1).upper().replace("Ü", "U")
    return org, m.group(2).split(":")[0]


def find_ungrounded_claims(
    text: str,
    grounding_facts: Union[str, GroundingSource],
    exempt_keywords=None,
    include_profile_concepts: bool = True,
) -> list:
    """Metindeki, grounding_facts ile desteklenmeyen iddiaları döndürür.

    Dönüş: okunur iddia string listesi (boş liste = temiz).
    Dil-bağımsız: TR + RU/UK/EN/DE tabloları birlikte taranır (hedef dil
    parametresi yok); sayı biçimi dilden bağımsızdır.
    grounding_facts: düz metin (etiketli satırlar GroundingSource.from_text ile
    ayrıştırılır) veya GroundingSource. Hizmet/marka ve sertifika dayanağı yalnız
    olumlu kaynak birimlerinden gelir; mutlak sonuç kalıpları kaynağa bakılmaz.
    """
    if not text or not text.strip():
        return []

    source = (
        grounding_facts if isinstance(grounding_facts, GroundingSource)
        else GroundingSource.from_text(grounding_facts or "")
    )
    working = _tr_lower(text)

    # Keyword muafiyeti DAR: yalnız SAYISAL taramada, asset içinde gerçekten
    # geçen tam keyword metni maskelenir. Süperlatif/garanti/hizmet/sertifika/
    # mutlak kalıplar maskesiz metinde çalışır (keyword'deki "en iyi" de yakalanır).
    numeric_text = working.replace("ı", "i")  # keyword eşleşmesi ı/i farkına (Implant) takılmasın
    for kw in exempt_keywords or []:
        kw_l = _tr_lower(str(kw)).strip().replace("ı", "i")
        if kw_l:
            # Sınır duyarlı: "20 yıllık implant" keyword'ü "120 yıllık ..." içinden maske açmaz;
            # sondaki Türkçe ek ("implantları", "hisseleri") serbest, bitişik rakam değil
            numeric_text = re.sub(
                r"(?<![^\W_])" + re.escape(kw_l) + r"(?!\d)", " ", numeric_text
            )

    facts_l = _tr_lower(source.facts_text())
    fact_numeric = {canon for _, canon in _extract_numeric_claims(facts_l)}
    # "100.000+ müşteri" iddiası fact'te "100.000 müşteri" olarak geçiyorsa
    # sayı bazında ground'lanır (+ eki abartı sayılmaz)
    fact_numbers = {c.split()[0].lstrip("%").rstrip("+") for c in fact_numeric}

    violations = []

    for span, canon in _extract_numeric_claims(numeric_text):
        if canon in fact_numeric:
            continue
        if canon.endswith("+") and canon.rstrip("+") in fact_numbers:
            continue
        violations.append(span)

    for pattern in _SUPERLATIVE_PATTERNS:
        for m in re.finditer(pattern, working):
            phrase = m.group(0)
            if phrase not in facts_l:
                violations.append(phrase)

    # RU/UK/EN/DE: 'ı'→'i' normalize metin (büyük I'lı İngilizce/Almanca sözcükler)
    working_i = working.replace("ı", "i")
    facts_i = facts_l.replace("ı", "i")
    for rx in _ML_SUPERLATIVE_COMPILED:
        for m in rx.finditer(working_i):
            phrase = m.group(0)
            if phrase not in facts_i:
                violations.append(phrase)

    # Kazanç garantisi HER DURUMDA yasak (grounding'e bakılmaz)
    for pattern in _GUARANTEE_PATTERNS:
        for m in re.finditer(pattern, working):
            violations.append(m.group(0))
    for rx in _ML_GUARANTEE_COMPILED:
        for m in rx.finditer(working_i):
            violations.append(m.group(0))

    violations.extend(_lexicon_violations(working, text, source, include_profile_concepts))

    # Tekilleştir, sırayı koru
    seen = set()
    unique = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


# ==================== NEGATİF ↔ HEDEF ÇAKIŞMASI (plan 2.1) ====================
# Bu kontrol Google'ın eşleştirmesini TAKLİT ETMEZ; yalnız açık metinsel
# çakışmaları broad / phrase / exact kelime kurallarıyla yakalar. Çoğul/ek
# (önek) eşitleme, fuzzy ve yazım hatası eşleştirmesi YOKTUR (masa != masaj).

_NEG_EDGE_CHARS = "[]()\"'“”‘’`-+.,;:!?"
_NEG_MATCH_TYPES = ("exact", "phrase", "broad")


def _negative_tokens(text: str) -> list:
    """Türkçe-duyarlı küçük harf + boşluk bölme + token kenar işareti temizliği.

    Google eşleşme türü sözdiziminin bıraktığı kenar işaretleri ([x], "x",
    baştaki '-') token kenarlarından atılır; token içi işaretler (2.el,
    türkiye'nin) korunur.
    """
    tokens = []
    for raw in _tr_lower(text or "").split():
        tok = raw.strip(_NEG_EDGE_CHARS)
        if tok:
            tokens.append(tok)
    return tokens


def _normalize_match_type(match_type) -> str:
    mt = _tr_lower(str(match_type or "")).strip()
    return mt if mt in _NEG_MATCH_TYPES else "broad"  # bilinmeyen → broad


def negative_blocks_target(negative: str, match_type: str, target: str) -> bool:
    """Negatif kelime hedef anahtar kelimeyi AÇIK metinsel olarak engelliyor mu?

    - broad: negatifin tüm kelimeleri hedefte (sıra önemsiz)
    - phrase: negatif kelimeleri hedefte aynı sırada ve bitişik
    - exact: kelime dizileri birebir aynı
    Bilinmeyen/eksik match_type → broad (düşürmeye en yatkın). Boş negatif
    veya boş hedef hiçbir şeyi engellemez.
    """
    neg = _negative_tokens(negative)
    tgt = _negative_tokens(target)
    if not neg or not tgt:
        return False
    rule = _normalize_match_type(match_type)
    if rule == "exact":
        return neg == tgt
    if rule == "phrase":
        n = len(neg)
        return any(tgt[i:i + n] == neg for i in range(len(tgt) - n + 1))
    return set(neg).issubset(set(tgt))


def negative_conflict(negative: str, match_type: str, targets) -> Optional[Tuple[str, str]]:
    """İlk çakışan (hedef, kural) çifti veya None."""
    for target in targets or []:
        if negative_blocks_target(negative, match_type, target):
            return str(target), _normalize_match_type(match_type)
    return None


def partition_negatives(negatives, targets, group_name: str = ""):
    """Negatifleri grubun KENDİ hedefleriyle çakışmaya göre ayırır.

    negatives: .keyword / .match_type taşıyan nesneler. Dönüş:
    (kept, dropped) — dropped, görünürlük için uyarı sözlükleri listesidir
    (type, group, negative, match_type, target, rule).
    """
    kept, dropped = [], []
    for n in negatives:
        hit = negative_conflict(n.keyword, n.match_type, targets)
        if hit is None:
            kept.append(n)
            continue
        target, rule = hit
        dropped.append({
            "type": "negative_dropped",
            "group": group_name,
            "negative": n.keyword,
            "match_type": n.match_type,
            "target": target,
            "rule": rule,
        })
    return kept, dropped


# ==================== KW KAPSAM ÖLÇÜMÜ (plan_ads_15kw_standardi §3) ====================

def kw_tokens(text: str) -> List[str]:
    """DKI varsayılanı açılır, Türkçe katlanır, alfanümerik olmayan her şey sınırdır."""
    text = re.sub(DKIValidator.VALID_PATTERN, r"\2", text or "")
    return re.findall(r"[^\W_]+", normalize_turkish(text))


def keyword_in_text(keyword: str, text: str) -> bool:
    """KW metinde bütün, ardışık, kelime sınırlı bir ifade olarak geçiyor mu?

    Bizim deterministik kapsam ölçümümüz — Google'ın Ad Strength hesabı değil.
    Türkçe ek toleransı bilinçli YOK ("laptop fiyatı" != "Laptop Fiyatları", K7).
    """
    kw = kw_tokens(keyword)
    if not kw:
        return False
    toks = kw_tokens(text)
    n = len(kw)
    return any(toks[i:i + n] == kw for i in range(len(toks) - n + 1))


# CTA = başlığın SON kelimesi bir eylem fiili (sektörden bağımsız; diş kliniğinde "İçin Danışın",
# "Planını İnceleyin" de CTA). Yalnız açık sonuç vaatleri dışlanır (Büyüyün, Yükselin, Kazanın).
# CTA olmak teslim edilebilir Türkçe demek DEĞİL: "Danışmanlık Al" CTA sayılır, dil kalitesi ayrı konu.
# shortcut: açık fiil listesi; ek/dilbilgisi filtresi bilinçli yok (iletişime/inceleme kırılıyordu).
_CTA_VERBS = frozenset(kw_tokens(
    "al alın ara arayın ulaşın geçin ver verin incele inceleyin keşfet keşfedin başvur başvurun "
    "başlayın başla deneyin dene isteyin iste edin edinin oluşturun planlayın öğrenin öğren "
    "tıklayın tıkla yakalayın seçin seç bulun karşılaştırın hesaplayın danışın indirin görün "
    "yararlanın faydalanın yaptırın kaydolun görüntüleyin tanışın"))


# Yabancı pazar CTA'sı: fiil cümle sonunda olmak zorunda değil. Her dil (mod, zarflar, fiiller):
#   lead = emir fiili başta (zarflardan sonra), any = emir kipi her yerde ayırt edici,
#   lead_or_end = Almanca (Sie-emri başta / mastar sonda). Ayıklama kw_tokens ile aynı katlanır.
# shortcut: açık liste + konum kuralı, dilbilgisi yok; EN "Call Center" gibi isim-baş başlık yanlış
# pozitif olabilir -> CTA sayımı yalnız tamamlama çağrısı/seçim önceliğini etkiler, upgrade: ölçümle.
_FOREIGN_CTA = {
    "İngilizce": ("lead", "now today please just easily simply instantly online",
                  "book call contact get start try learn discover order buy schedule request "
                  "sign-up shop explore find claim join visit view compare download enquire "
                  "inquire apply reserve speak talk ask browse grab calculate subscribe register "
                  "check-out reach"),
    "Almanca": ("lead_or_end", "jetzt heute bitte direkt einfach sofort online kostenlos",
                "buchen anrufen kontaktieren anfragen entdecken bestellen sichern starten "
                "termin-vereinbaren vereinbaren anfordern testen ausprobieren erfahren berechnen "
                "vergleichen reservieren anmelden kennenlernen mehr-erfahren "
                "kontaktieren-sie rufen-sie fordern-sie buchen-sie sichern-sie entdecken-sie "
                "bestellen-sie vereinbaren-sie testen-sie erfahren-sie starten-sie lassen-sie-sich "
                "informieren-sie-sich"),
    "Rusça": ("any", "",
              "запишитесь позвоните звоните закажите заказывайте узнайте получите начните "
              "свяжитесь оформите попробуйте оставьте рассчитайте выберите приходите обращайтесь "
              "подберите забронируйте купите скачайте зарегистрируйтесь проверьте сравните "
              "откройте заходите"),
    "Ukraynaca": ("any", "",
                  "запишіться записуйтесь зателефонуйте телефонуйте дзвоніть замовте замовляйте "
                  "дізнайтеся дізнайтесь отримайте почніть зв'яжіться звʼяжіться оформіть "
                  "спробуйте залиште розрахуйте оберіть виберіть забронюйте купіть купуйте "
                  "скористайтеся звертайтеся підберіть завантажте зареєструйтеся перевірте "
                  "порівняйте відкрийте приходьте"),
}


def _compile_foreign_cta() -> dict:
    out = {}
    for lang, (mode, adverbs, verbs) in _FOREIGN_CTA.items():
        # "-" çok kelimeli ifadeyi bağlar; kw_tokens zaten tire/kesmeyi sınır yapar
        phrases = {tuple(kw_tokens(v.replace("-", " "))) for v in verbs.split()}
        out[lang] = (mode, frozenset(kw_tokens(adverbs)), phrases)
    return out


_FOREIGN_CTA_COMPILED = _compile_foreign_cta()


def is_cta(text: str, language: Optional[str] = None) -> bool:
    """CTA mı? Türkçe/None: başlığın son kelimesi eylem fiili ("Göz Atın" dahil) — bayt-aynı
    eski davranış. EN/DE/RU/UK: dile özgü emir/mastar tablosu (bkz. _FOREIGN_CTA)."""
    t = kw_tokens(text)
    rule = _FOREIGN_CTA_COMPILED.get(language or "")
    if rule is None:
        return bool(t) and (t[-1] in _CTA_VERBS or t[-2:] == ["goz", "atin"])
    mode, adverbs, phrases = rule
    lead = list(t)
    while lead and lead[0] in adverbs:
        lead.pop(0)
    for p in phrases:
        n = len(p)
        if mode != "any" and (tuple(lead[:n]) == p or (mode == "lead_or_end" and tuple(t[-n:]) == p)):
            return True
        if mode == "any" and any(tuple(t[i:i + n]) == p for i in range(len(t) - n + 1)):
            return True
    return False


def keyword_coverage(
    target_keywords: List[str], headlines: List[str], descriptions: List[str]
) -> Dict[str, str]:
    """Her hedef KW için "headline" | "description" | "none" (girdi sırası korunur)."""
    result: Dict[str, str] = {}
    for kw in target_keywords:
        if any(keyword_in_text(kw, h) for h in headlines):
            result[kw] = "headline"
        elif any(keyword_in_text(kw, d) for d in descriptions):
            result[kw] = "description"
        else:
            result[kw] = "none"
    return result


def build_standard_report(
    target_keywords: List[str],
    target_keyword_ids: List[int],
    fill_keyword_ids: Optional[List[int]],
    headlines: List[str],
    descriptions: List[str],
    language: Optional[str] = None,
) -> AdGroupStandardReport:
    """15 KW teslim standardı raporu (okuma + export için TEK hesap yeri).

    Yalnız olgu ve liste: eksik başlık / KW'li açıklama / CTA "standart altı" yapar;
    KW kapsam eksikliği yapmaz (K4). NULL/boş fill -> fill_keywords boş.
    """
    coverage = keyword_coverage(target_keywords, headlines, descriptions)
    kw_descriptions = sum(
        any(keyword_in_text(kw, d) for kw in target_keywords) for d in descriptions
    )
    missing_headlines = max(0, ADS_HEADLINES_PER_GROUP - len(headlines))
    missing_kw_descriptions = max(0, ADS_DESCRIPTIONS_PER_GROUP - kw_descriptions)
    cta_count = sum(is_cta(h, language) for h in headlines)
    missing_cta = max(0, ADS_MIN_CTA_HEADLINES - cta_count)
    fill_ids = set(fill_keyword_ids or [])
    short = [k for k in target_keywords if len(k) <= 30]
    return AdGroupStandardReport(
        keyword_count=len(target_keywords),
        headline_count=len(headlines),
        kw_description_count=kw_descriptions,
        missing_headlines=missing_headlines,
        missing_kw_descriptions=missing_kw_descriptions,
        cta_count=cta_count,
        missing_cta=missing_cta,
        below_standard=bool(missing_headlines or missing_kw_descriptions or missing_cta),
        pool_below_target=len(target_keywords) < ADS_KEYWORDS_PER_GROUP,
        keywords_in_headlines=[k for k in target_keywords if coverage[k] == "headline"],
        keywords_description_only=[k for k in target_keywords if coverage[k] == "description"],
        keywords_unused=[k for k in target_keywords if coverage[k] == "none"],
        short_keyword_count=len(short),
        short_keywords_in_headlines=sum(coverage[k] == "headline" for k in short),
        unused_keyword_reasons={
            k: ("too_long" if len(k) > 30 else "claim" if find_ungrounded_claims(k, "", include_profile_concepts=False) else "not_used")
            for k in target_keywords if coverage[k] != "headline"
        },
        fill_keywords=[k for k, i in zip(target_keywords, target_keyword_ids) if i in fill_ids],
    )
