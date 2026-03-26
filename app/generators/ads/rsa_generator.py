"""
RSA Generator for Google Ads.

Generates complete RSA (Responsive Search Ads) components:
- Headlines (3-15, max 30 chars)
- Descriptions (2-4, max 90 chars)
- Negative Keywords (10+)
"""
import json
import re
from typing import List, Dict, Optional, Tuple, Any
from loguru import logger

from app.generators.ai_service import AIService
from app.generators.ads.prompt_templates import ADS_RSA_GENERATION_PROMPT
from app.generators.ads.validators import HeadlineValidator, DescriptionValidator, DKIValidator
from app.schemas.ads import (
    KeywordGroupSchema,
    HeadlineSchema,
    DescriptionSchema,
    NegativeKeywordSchema,
    AdGroupFullSchema,
)


class RSAGenerator:
    """
    Generates RSA components for a single ad group.
    
    Includes validation:
    - 3-phase headline validation
    - Sentence-aware description truncation
    - DKI format validation
    """
    
    MIN_HEADLINES = 3
    MAX_HEADLINES = 15
    MIN_DESCRIPTIONS = 2
    MAX_DESCRIPTIONS = 4
    MIN_NEGATIVE_KEYWORDS = 10
    HEADLINE_FILL_MAX_ITERATIONS = 12
    DESCRIPTION_FILL_MAX_ITERATIONS = 8
    RSA_MAX_TOKENS = 3500
    MAX_AI_DESCRIPTION_SHORTEN_PER_GROUP = 2
    RSA_RESPONSE_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "headlines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string"},
                        "position": {"type": "string"},
                    },
                    "required": ["text", "type", "position"]
                }
            },
            "descriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string"},
                        "position": {"type": "string"},
                    },
                    "required": ["text", "type", "position"]
                }
            },
            "negative_keywords": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "match_type": {"type": "string"},
                        "category": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["keyword", "match_type", "category", "reason"]
                }
            }
        },
        "required": ["headlines", "descriptions", "negative_keywords"]
    }
    
    def __init__(
        self, 
        ai_service: AIService,
        enable_ai_regeneration: bool = True,
        enable_fallback: bool = True
    ):
        self.ai_service = ai_service
        self.enable_ai_regeneration = enable_ai_regeneration
        self.enable_fallback = enable_fallback
        
        # Initialize validators
        self.headline_validator = HeadlineValidator()
        self.description_validator = DescriptionValidator()
        self.dki_validator = DKIValidator()
    
    def generate_rsa(
        self,
        group: KeywordGroupSchema,
        brand_name: str = "",
        brand_usp: str = ""
    ) -> AdGroupFullSchema:
        """
        Generate complete RSA set for an ad group.
        
        Args:
            group: Keyword group to generate RSA for
            brand_name: Brand name for personalization
            brand_usp: Unique selling proposition
            
        Returns:
            AdGroupFullSchema with validated headlines, descriptions, negatives
        """
        logger.info(f"Generating RSA for group: '{group.name}' ({len(group.keywords)} keywords)")
        
        # Get primary keyword for validation context
        primary_keyword = group.keywords[0] if group.keywords else ""
        
        # Primary attempt
        headlines, descriptions, negatives, headline_stats = self._generate_and_validate_components(
            group=group,
            brand_name=brand_name,
            brand_usp=brand_usp,
            primary_keyword=primary_keyword,
            stage="primary",
            strict_prompt=False
        )

        # Retry once with strict prompt
        if self._below_minimum(headlines, descriptions) and self.enable_ai_regeneration:
            logger.warning(
                f"Minimum assets not met for group '{group.name}' "
                f"(headlines={len(headlines)}, descriptions={len(descriptions)}), retrying once"
            )
            headlines, descriptions, negatives, headline_stats = self._generate_and_validate_components(
                group=group,
                brand_name=brand_name,
                brand_usp=brand_usp,
                primary_keyword=primary_keyword,
                stage="retry",
                strict_prompt=True
            )

        # Deterministic fallback
        if self._below_minimum(headlines, descriptions):
            if self.enable_fallback:
                logger.warning(
                    f"Using deterministic fallback for group '{group.name}' "
                    f"(headlines={len(headlines)}, descriptions={len(descriptions)})"
                )
                headlines, descriptions, negatives, headline_stats = self._build_deterministic_fallback(
                    group=group,
                    brand_name=brand_name,
                    brand_usp=brand_usp,
                    primary_keyword=primary_keyword
                )
            else:
                raise ValueError(
                    f"Group '{group.name}': only {len(headlines)} valid headlines and "
                    f"{len(descriptions)} valid descriptions after retry. Group will not be saved."
                )

        # Guardrails after fallback
        if self._below_minimum(headlines, descriptions):
            raise ValueError(
                f"Group '{group.name}': fallback still below minimum "
                f"(headlines={len(headlines)}, descriptions={len(descriptions)})."
            )

        if len(negatives) < self.MIN_NEGATIVE_KEYWORDS:
            logger.warning(f"Only {len(negatives)} negative keywords, adding defaults")
            negatives = self._add_default_negatives(negatives)
        
        return AdGroupFullSchema(
            name=group.name,
            theme=group.theme,
            keyword_ids=group.keyword_ids,
            keywords=group.keywords,
            headlines=headlines,
            descriptions=descriptions,
            negative_keywords=negatives,
            headlines_generated=headline_stats["total"],
            headlines_eliminated=headline_stats["eliminated"],
            headlines_shortened=headline_stats["shortened"],
            headlines_regenerated=headline_stats["regenerated"],
            dki_converted_count=headline_stats["dki_converted"],
        )

    def _below_minimum(
        self,
        headlines: List[HeadlineSchema],
        descriptions: List[DescriptionSchema]
    ) -> bool:
        return len(headlines) < self.MIN_HEADLINES or len(descriptions) < self.MIN_DESCRIPTIONS

    def _generate_and_validate_components(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        primary_keyword: str,
        stage: str,
        strict_prompt: bool
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema], List[NegativeKeywordSchema], Dict[str, int]]:
        raw_response = self._generate_raw_rsa(
            group=group,
            brand_name=brand_name,
            brand_usp=brand_usp,
            strict_prompt=strict_prompt
        )
        headlines_raw, descriptions_raw, negatives_raw = self._parse_rsa_response(
            response=raw_response,
            group_name=group.name,
            stage=stage
        )
        headlines, headline_stats = self._validate_headlines(headlines_raw, primary_keyword)
        descriptions, _ = self._validate_descriptions(
            descriptions_raw=descriptions_raw,
            ai_service=self.ai_service if self.enable_ai_regeneration else None,
            enable_ai_shortening=self.enable_ai_regeneration
        )
        negatives = self._parse_negatives(negatives_raw)
        return headlines, descriptions, negatives, headline_stats
    
    def _generate_raw_rsa(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        strict_prompt: bool = False
    ) -> str:
        """Generate raw RSA content via AI."""
        brand_name = (brand_name or "Marka").strip() or "Marka"
        brand_usp = (brand_usp or "Kaliteli ürün ve hizmet").strip() or "Kaliteli ürün ve hizmet"

        if strict_prompt:
            prompt = self._build_strict_retry_prompt(group, brand_name, brand_usp)
        else:
            prompt = ADS_RSA_GENERATION_PROMPT.format(
                group_name=group.name,
                group_theme=group.theme,
                keywords=", ".join(group.keywords),
                brand_name=brand_name,
                brand_usp=brand_usp
            )
        
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=self.RSA_MAX_TOKENS,
            response_schema=self.RSA_RESPONSE_SCHEMA
        )
        return response

    def _build_strict_retry_prompt(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str
    ) -> str:
        keywords = ", ".join(group.keywords)
        return (
            "Google Ads RSA için SADECE geçerli JSON üret.\n"
            "Markdown, açıklama, kod fence veya tablo yazma.\n"
            f"Grup: {group.name}\n"
            f"Tema: {group.theme}\n"
            f"Anahtar Kelimeler: {keywords}\n"
            f"Marka: {brand_name}\n"
            f"USP: {brand_usp}\n\n"
            "Zorunlu platform limitleri:\n"
            "- Headlines: 3-15 adet, her biri <=30 karakter\n"
            "- Descriptions: 2-4 adet, her biri <=90 karakter\n"
            "- Negative keywords: en az 10 adet\n\n"
            "JSON ŞEMASI:\n"
            "{\n"
            '  "headlines": [{"text":"", "type":"keyword|cta|benefit|trust|dynamic", "position":"any|position_1|position_2|position_3"}],\n'
            '  "descriptions": [{"text":"", "type":"value_prop|features|cta|trust", "position":"any|position_1|position_2|position_3"}],\n'
            '  "negative_keywords": [{"keyword":"", "match_type":"phrase|exact|broad", "category":"", "reason":""}]\n'
            "}"
        )
    
    def _parse_rsa_response(
        self,
        response: str,
        group_name: str,
        stage: str
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Parse AI response into raw components."""
        try:
            if '```' in response:
                response = re.sub(r'```(?:json)?\s*', '', response).strip()
            data = json.loads(response)
            if not isinstance(data, dict):
                raise ValueError("RSA response root must be a JSON object")
            if not isinstance(data.get("headlines"), list):
                raise ValueError("RSA response missing 'headlines' array")
            if not isinstance(data.get("descriptions"), list):
                raise ValueError("RSA response missing 'descriptions' array")
            if not isinstance(data.get("negative_keywords"), list):
                raise ValueError("RSA response missing 'negative_keywords' array")
            
            headlines = data.get("headlines", [])
            descriptions = data.get("descriptions", [])
            negatives = data.get("negative_keywords", [])
            
            return headlines, descriptions, negatives
            
        except json.JSONDecodeError as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"RSA parse failed | group_name={group_name} | stage={stage} | "
                f"exception_type={type(e).__name__} | snippet={snippet}"
            )
            return [], [], []
        except Exception as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"RSA validation failed | group_name={group_name} | stage={stage} | "
                f"exception_type={type(e).__name__} | snippet={snippet}"
            )
            return [], [], []
    
    def _validate_headlines(
        self, 
        headlines_raw: List[Dict],
        primary_keyword: str
    ) -> Tuple[List[HeadlineSchema], Dict[str, int]]:
        """
        Validate headlines with 3-phase validation.
        
        Returns:
            Tuple of (validated headlines, stats dict)
        """
        validated = []
        stats = {
            "total": len(headlines_raw),
            "kept": 0,
            "shortened": 0,
            "regenerated": 0,
            "eliminated": 0,
            "dki_converted": 0,
        }
        
        for i, h in enumerate(headlines_raw):
            text = h.get("text", "")
            h_type = h.get("type", "keyword")
            position = h.get("position", "any")
            
            if not text:
                stats["eliminated"] += 1
                continue
            
            # Step 1: DKI validation (if dynamic type)
            is_dki = False
            if h_type == "dynamic" or "{" in text:
                text, is_dki, dki_reason = self.dki_validator.validate(text)
                if dki_reason:
                    stats["dki_converted"] += 1
                    h_type = "keyword"  # Downgrade type if DKI was invalid
            
            # Step 2: Character limit validation
            validated_text, action, reason = self.headline_validator.validate(
                headline=text,
                keyword=primary_keyword,
                ai_service=self.ai_service if self.enable_ai_regeneration else None,
                enable_regeneration=self.enable_ai_regeneration
            )
            
            # Track stats
            stats[action] = stats.get(action, 0) + 1
            
            if validated_text:
                validated.append(HeadlineSchema(
                    text=validated_text,
                    type=h_type,
                    position_preference=position,
                    is_dki=is_dki
                ))
        
        if len(validated) > self.MAX_HEADLINES:
            validated = validated[:self.MAX_HEADLINES]

        logger.info(f"Headlines: {stats['total']} total, {len(validated)} valid, "
                   f"{stats['eliminated']} eliminated, {stats['dki_converted']} DKI fixed")
        
        return validated, stats
    
    def _validate_descriptions(
        self, 
        descriptions_raw: List[Dict],
        ai_service: Optional[AIService] = None,
        enable_ai_shortening: bool = True
    ) -> Tuple[List[DescriptionSchema], Dict[str, int]]:
        """
        Validate descriptions with sentence-aware and optional AI shortening.
        """
        validated = []
        stats = {
            "total": len(descriptions_raw),
            "kept": 0,
            "sentence_trim": 0,
            "ai_shortened": 0,
            "truncated": 0,
        }
        ai_attempts_remaining = self.MAX_AI_DESCRIPTION_SHORTEN_PER_GROUP
        
        for d in descriptions_raw:
            text = d.get("text", "")
            d_type = d.get("type", "value_prop")
            position = d.get("position", "any")
            
            if not text:
                continue
            
            allow_ai_this_item = bool(
                ai_service and enable_ai_shortening and ai_attempts_remaining > 0
            )
            validated_text, action, reason = self.description_validator.validate(
                text,
                ai_service=ai_service if allow_ai_this_item else None,
                enable_ai_shortening=allow_ai_this_item
            )
            ai_attempted = (
                action == "ai_shortened" or
                (action == "truncated" and reason == "AI shortening failed, word-boundary fallback")
            )
            if ai_attempted and ai_attempts_remaining > 0:
                ai_attempts_remaining -= 1

            stats[action] = stats.get(action, 0) + 1

            if not validated_text:
                continue
            
            validated.append(DescriptionSchema(
                text=validated_text,
                type=d_type,
                position_preference=position
            ))
        
        if len(validated) > self.MAX_DESCRIPTIONS:
            validated = validated[:self.MAX_DESCRIPTIONS]

        logger.info(
            f"Descriptions: {stats['total']} total, {len(validated)} valid, "
            f"{stats['ai_shortened']} ai_shortened, {stats['truncated']} truncated"
        )
        
        return validated, stats
    
    def _parse_negatives(
        self, 
        negatives_raw: List[Dict]
    ) -> List[NegativeKeywordSchema]:
        """Parse negative keywords."""
        negatives = []
        
        for n in negatives_raw:
            keyword = n.get("keyword", "")
            if not keyword:
                continue
            
            negatives.append(NegativeKeywordSchema(
                keyword=keyword,
                match_type=n.get("match_type", "phrase"),
                category=n.get("category"),
                reason=n.get("reason")
            ))
        
        return negatives

    def _build_deterministic_fallback(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        primary_keyword: str
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema], List[NegativeKeywordSchema], Dict[str, int]]:
        brand_name = (brand_name or "Marka").strip() or "Marka"
        brand_usp = (brand_usp or "Kaliteli ürün ve hizmet").strip() or "Kaliteli ürün ve hizmet"
        keywords = [k.strip() for k in group.keywords if k and k.strip()]
        first_keyword = keywords[0] if keywords else primary_keyword or group.name
        second_keyword = keywords[1] if len(keywords) > 1 else first_keyword

        headline_candidates = [
            {"text": first_keyword, "type": "keyword", "position": "position_1"},
            {"text": f"{brand_name} {second_keyword}", "type": "keyword", "position": "any"},
            {"text": "Hemen İncele", "type": "cta", "position": "position_3"},
            {"text": f"{brand_name} Güvencesi", "type": "trust", "position": "any"},
            {"text": "Kaliteli Ürün Seçenekleri", "type": "benefit", "position": "any"},
        ]
        description_candidates = [
            {
                "text": f"{brand_name} ile {first_keyword} seçeneklerini keşfedin. Hemen inceleyin.",
                "type": "value_prop",
                "position": "any"
            },
            {
                "text": f"{brand_usp}. Güvenli alışveriş ve hızlı teslimat fırsatını kaçırmayın.",
                "type": "cta",
                "position": "any"
            }
        ]

        headlines, headline_stats = self._validate_headlines(headline_candidates, first_keyword)
        descriptions, _ = self._validate_descriptions(description_candidates)
        negatives = self._add_default_negatives([])

        # Hard guarantees for minimum outputs.
        headline_fill_iterations = 0
        while len(headlines) < self.MIN_HEADLINES and headline_fill_iterations < self.HEADLINE_FILL_MAX_ITERATIONS:
            headline_fill_iterations += 1
            idx = len(headlines) + 1
            fallback_text = f"{brand_name} Ürünleri {idx}"
            validated_text, _, _ = self.headline_validator.validate(
                fallback_text,
                first_keyword,
                ai_service=None,
                enable_regeneration=False
            )
            if not validated_text:
                validated_text = fallback_text[:self.headline_validator.MAX_LENGTH].strip()
            if not validated_text:
                validated_text = (brand_name or "Marka")[:self.headline_validator.MAX_LENGTH].strip()
            if not validated_text:
                continue
            headlines.append(HeadlineSchema(
                text=validated_text,
                type="keyword",
                position_preference="any",
                is_dki=False
            ))

        if len(headlines) < self.MIN_HEADLINES:
            raise ValueError(
                f"Deterministic fallback could not build minimum headlines "
                f"(built={len(headlines)}, required={self.MIN_HEADLINES})."
            )

        description_fill_templates = [
            f"{brand_name} ürünlerini hemen inceleyin ve size uygun seçeneği bulun.",
            f"{brand_usp}. Hemen keşfedin ve hızlı teslimat fırsatından yararlanın.",
            f"{first_keyword} seçeneklerini karşılaştırın, sizin için doğru ürünü şimdi seçin.",
            f"{brand_name} kalitesiyle güvenli alışveriş deneyimini hemen başlatın."
        ]
        description_fill_iterations = 0
        while len(descriptions) < self.MIN_DESCRIPTIONS and description_fill_iterations < self.DESCRIPTION_FILL_MAX_ITERATIONS:
            text = description_fill_templates[description_fill_iterations % len(description_fill_templates)]
            description_fill_iterations += 1
            validated_text, _, _ = self.description_validator.validate(text)
            if not validated_text:
                validated_text = text[:self.description_validator.MAX_LENGTH].strip()
            if not validated_text:
                continue
            descriptions.append(DescriptionSchema(
                text=validated_text,
                type="value_prop",
                position_preference="any"
            ))

        if len(descriptions) < self.MIN_DESCRIPTIONS:
            raise ValueError(
                f"Deterministic fallback could not build minimum descriptions "
                f"(built={len(descriptions)}, required={self.MIN_DESCRIPTIONS})."
            )

        return headlines, descriptions, negatives, headline_stats
    
    def _add_default_negatives(
        self, 
        existing: List[NegativeKeywordSchema]
    ) -> List[NegativeKeywordSchema]:
        """Add default negative keywords if below minimum."""
        defaults = [
            ("nedir", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("nasıl", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("ne demek", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("ücretsiz", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("bedava", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("free", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("ucuz", "phrase", "dusuk_kalite", "Düşük kalite algısı"),
            ("ikinci el", "phrase", "ikinci_el", "Yeni ürün satışı"),
            ("şikayet", "phrase", "sikayet", "Negatif arama"),
            ("tamir", "phrase", "diy", "Hizmet değil bilgi arıyor"),
        ]
        
        existing_keywords = {n.keyword.lower() for n in existing}
        
        result = list(existing)
        for keyword, match_type, category, reason in defaults:
            if keyword.lower() not in existing_keywords:
                result.append(NegativeKeywordSchema(
                    keyword=keyword,
                    match_type=match_type,
                    category=category,
                    reason=reason
                ))
        
        return result
