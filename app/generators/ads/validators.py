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

from app.generators.ai_service import scoped
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
)

# Birim eş anlamlıları — "10 yıl deneyim" fact'i "10 yıldır hizmet"
# iddiasını ground'lar (aynı kavram, farklı çekim)
_UNIT_CANON = {
    "yildir": "yıl", "yıldır": "yıl", "yil": "yıl", "yıllık": "yıl",
    "yillik": "yıl", "musteri": "müşteri", "yatirimci": "yatırımcı",
    "kullanici": "kullanıcı", "kisi": "kişi", "uye": "üye",
    "degerlendirme": "değerlendirme", "ulke": "ülke", "yildiz": "yıldız",
}


def _canon_unit(unit: str) -> str:
    return _UNIT_CANON.get(unit, unit)

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

_GUARANTEE_PATTERNS = (
    r"kazan[çc]\s+garanti",
    r"garantili\s+kazan[çc]",
    r"k[âa]r\s+garanti",
    r"garantili\s+getiri",
    r"getiri\s+garanti",
    r"kesin\s+kazan[çc]",
)


def _tr_lower(text: str) -> str:
    """Türkçe-güvenli lowercase (I→ı, İ→i)."""
    return text.replace("I", "ı").replace("İ", "i").lower()


def _canonical_number(raw: str) -> str:
    """Sayı biçimlerini tek biçime indirger: '50.000'/'50,000'/'50 bin' → '50000'."""
    s = _tr_lower(raw).strip()
    multiplier = 1
    for word, mult in (("milyon", 1_000_000), ("bin", 1_000)):
        if word in s:
            s = s.replace(word, "").strip()
            multiplier = mult
            break
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


# Sayı gövdesi: 50 | 50.000 | 4,8 | 50 bin | 3 milyon
_NUMBER_BODY = r"\d[\d.,]*(?:\s*(?:bin|milyon))?"


def _extract_numeric_claims(text: str) -> list:
    """Metindeki sayısal iddia adaylarını (span, canonical) döndürür."""
    lowered = _tr_lower(text)
    claims = []

    # 4.8/5 (puan) rating biçimi — önce yakala ve metinden çıkar,
    # yoksa "/5 puan" parçası ayrı bir iddia gibi görünür.
    # FALSE-POSITIVE korumasi (idea-44 dersi): thread/carousel sayfalama
    # numaralari ("1/8", "2/8") rating DEGILDIR — yalnizca birim ("puan/
    # yildiz") VEYA ondalikli pay ("4.8/5") tasiyan N/M rating sayilir;
    # duz tam-sayi/tam-sayi oldugu gibi birakilir.
    def _consume_rating(m):
        num, unit = m.group(1), m.group(2)
        if unit or "." in num or "," in num:
            claims.append((m.group(0).strip(), _canonical_number(num) + " puan"))
            return " "
        return m.group(0)  # sayfalama (1/8) — iddia degil

    lowered = re.sub(
        r"(\d[\d.,]*)\s*/\s*\d+(\s*(?:puan|yıldız|yildiz))?",
        _consume_rating,
        lowered,
    )

    # yüzde 80 / %80
    for m in re.finditer(rf"(?:%\s*|yüzde\s+|yuzde\s+)({_NUMBER_BODY})", lowered):
        claims.append((m.group(0).strip(), "%" + _canonical_number(m.group(1))))

    # 100.000+ (birimli veya birimsiz artı)
    for m in re.finditer(rf"({_NUMBER_BODY})\s*\+", lowered):
        claims.append((m.group(0).strip(), _canonical_number(m.group(1)) + "+"))

    # sayı + birim (10 yıl, 4.8 puan, 50 bin müşteri)
    unit_alt = "|".join(_CLAIM_UNIT_TERMS)
    for m in re.finditer(rf"({_NUMBER_BODY})\s*\+?\s*({unit_alt})", lowered):
        claims.append(
            (
                m.group(0).strip(),
                _canonical_number(m.group(1)) + " " + _canon_unit(m.group(2)),
            )
        )

    return claims


def find_ungrounded_claims(
    text: str,
    grounding_facts: str,
    exempt_keywords=None,
) -> list:
    """Metindeki, grounding_facts ile desteklenmeyen iddiaları döndürür.

    Dönüş: okunur iddia string listesi (boş liste = temiz).
    """
    if not text or not text.strip():
        return []

    working = _tr_lower(text)

    # Keyword muafiyeti DAR: yalnız asset içinde gerçekten geçen tam
    # keyword metni taramadan çıkarılır.
    for kw in exempt_keywords or []:
        kw_l = _tr_lower(str(kw)).strip()
        if kw_l and kw_l in working:
            working = working.replace(kw_l, " ")

    facts_l = _tr_lower(grounding_facts or "")
    fact_numeric = {canon for _, canon in _extract_numeric_claims(facts_l)}
    # "100.000+ müşteri" iddiası fact'te "100.000 müşteri" olarak geçiyorsa
    # sayı bazında ground'lanır (+ eki abartı sayılmaz)
    fact_numbers = {c.split()[0].lstrip("%").rstrip("+") for c in fact_numeric}

    violations = []

    for span, canon in _extract_numeric_claims(working):
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

    # Kazanç garantisi HER DURUMDA yasak (grounding'e bakılmaz)
    for pattern in _GUARANTEE_PATTERNS:
        for m in re.finditer(pattern, working):
            violations.append(m.group(0))

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
