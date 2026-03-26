"""
Keyword Grouper for Google Ads.

Groups keywords by purchase intent for optimized ad groups.
Uses AI to semantically cluster keywords.
"""
import json
import re
from typing import List, Dict, Optional, Any
from loguru import logger

from app.generators.ai_service import AIService
from app.generators.ads.prompt_templates import ADS_GROUPING_PROMPT
from app.schemas.ads import KeywordForGrouping, KeywordGroupSchema, GroupingResponse


class KeywordGrouper:
    """
    Groups keywords into ad groups based on purchase intent.
    
    Rules:
    - 3-7 keywords per group
    - Same intent in same group
    - Descriptive group names
    """
    
    MIN_KEYWORDS_PER_GROUP = 3
    MAX_KEYWORDS_PER_GROUP = 7
    GROUPING_MAX_TOKENS = 2500
    GROUPING_RESPONSE_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "ad_groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "theme": {"type": "string"},
                        "keyword_ids": {
                            "type": "array",
                            "items": {"type": "integer"}
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["name", "theme", "keyword_ids", "keywords"]
                }
            }
        },
        "required": ["ad_groups"]
    }
    
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
    
    def group_keywords(
        self, 
        keywords: List[KeywordForGrouping]
    ) -> List[KeywordGroupSchema]:
        """
        Group keywords using AI.
        
        Args:
            keywords: List of keywords with id and text
            
        Returns:
            List of KeywordGroupSchema with grouped keywords
        """
        if not keywords:
            logger.warning("No keywords provided for grouping")
            return []
        
        # If very few keywords, put them all in one group
        if len(keywords) <= self.MIN_KEYWORDS_PER_GROUP:
            return self._create_single_group(keywords)
        
        # Prepare keywords for AI
        keywords_json = json.dumps(
            [{"id": k.id, "keyword": k.keyword} for k in keywords],
            ensure_ascii=False,
            indent=2
        )
        
        # Generate groups via AI
        prompt = ADS_GROUPING_PROMPT.format(keywords_json=keywords_json)

        try:
            validated_groups = self._generate_and_validate_groups(
                prompt=prompt,
                keywords=keywords,
                stage="primary"
            )
            logger.info(f"Created {len(validated_groups)} ad groups from {len(keywords)} keywords")
            return validated_groups
        except Exception as e:
            logger.warning(f"AI grouping primary attempt failed: {e}")

        strict_prompt = self._build_strict_retry_prompt(keywords_json)
        try:
            validated_groups = self._generate_and_validate_groups(
                prompt=strict_prompt,
                keywords=keywords,
                stage="retry"
            )
            logger.info(f"Created {len(validated_groups)} ad groups from {len(keywords)} keywords (retry)")
            return validated_groups
        except Exception as e:
            logger.error(f"AI grouping retry failed: {e}")
            return self._fallback_grouping(keywords)

    def _generate_and_validate_groups(
        self,
        prompt: str,
        keywords: List[KeywordForGrouping],
        stage: str
    ) -> List[KeywordGroupSchema]:
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=self.GROUPING_MAX_TOKENS,
            response_schema=self.GROUPING_RESPONSE_SCHEMA
        )
        groups = self._parse_grouping_response(response, keywords, stage=stage)
        return self._validate_groups(groups, keywords)

    def _build_strict_retry_prompt(self, keywords_json: str) -> str:
        return (
            "Aşağıdaki anahtar kelimeleri 3-7 kelimelik reklam gruplarına ayır.\n"
            "Satın alma niyeti benzer kelimeleri birlikte tut.\n"
            "Sadece JSON döndür. Markdown, açıklama veya tablo yazma.\n\n"
            "JSON ŞEMASI:\n"
            "{\n"
            '  "ad_groups": [\n'
            "    {\n"
            '      "name": "Açıklayıcı Grup İsmi",\n'
            '      "theme": "Ortak niyet",\n'
            '      "keyword_ids": [1,2,3],\n'
            '      "keywords": ["kelime1","kelime2","kelime3"]\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "ANAHTAR KELİMELER:\n"
            f"{keywords_json}"
        )
    
    def _parse_grouping_response(
        self,
        response: str,
        original_keywords: List[KeywordForGrouping],
        stage: str = "primary"
    ) -> List[KeywordGroupSchema]:
        """Parse AI response into KeywordGroupSchema list."""
        try:
            if '```' in response:
                response = re.sub(r'```(?:json)?\s*', '', response).strip()
            data = json.loads(response)
            if not isinstance(data, dict):
                raise ValueError("Grouping response root must be a JSON object")
            if not isinstance(data.get("ad_groups"), list):
                raise ValueError("Grouping response missing 'ad_groups' array")
            groups = []
            
            for group_data in data.get("ad_groups", []):
                if not isinstance(group_data, dict):
                    continue
                group = KeywordGroupSchema(
                    name=group_data.get("name", "Unnamed Group"),
                    theme=group_data.get("theme", ""),
                    keyword_ids=group_data.get("keyword_ids", []),
                    keywords=group_data.get("keywords", [])
                )
                groups.append(group)

            if not groups:
                raise ValueError("Grouping response contains no ad groups")
            
            return groups
            
        except json.JSONDecodeError as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"Grouping parse failed | stage={stage} | exception_type={type(e).__name__} | "
                f"snippet={snippet}"
            )
            raise
        except Exception as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"Grouping validation failed | stage={stage} | exception_type={type(e).__name__} | "
                f"snippet={snippet}"
            )
            raise
    
    def _validate_groups(
        self, 
        groups: List[KeywordGroupSchema],
        original_keywords: List[KeywordForGrouping]
    ) -> List[KeywordGroupSchema]:
        """
        Validate groups and ensure all keywords are included.
        """
        validated = []
        used_keyword_ids = set()
        original_ids = {k.id for k in original_keywords}
        
        for group in groups:
            # Validate keyword IDs exist
            valid_ids = [kid for kid in group.keyword_ids if kid in original_ids]
            
            if len(valid_ids) < self.MIN_KEYWORDS_PER_GROUP:
                logger.warning(f"Group '{group.name}' has too few keywords ({len(valid_ids)}), will merge later")
                continue
            
            if len(valid_ids) > self.MAX_KEYWORDS_PER_GROUP:
                # Split into smaller groups
                chunks = [valid_ids[i:i+self.MAX_KEYWORDS_PER_GROUP] 
                         for i in range(0, len(valid_ids), self.MAX_KEYWORDS_PER_GROUP)]
                
                for i, chunk in enumerate(chunks):
                    chunk_keywords = [k.keyword for k in original_keywords if k.id in chunk]
                    split_group = KeywordGroupSchema(
                        name=f"{group.name} - Part {i+1}",
                        theme=group.theme,
                        keyword_ids=chunk,
                        keywords=chunk_keywords
                    )
                    validated.append(split_group)
                    used_keyword_ids.update(chunk)
            else:
                group.keyword_ids = valid_ids
                group.keywords = [k.keyword for k in original_keywords if k.id in valid_ids]
                validated.append(group)
                used_keyword_ids.update(valid_ids)
        
        # Check for orphan keywords
        orphan_ids = original_ids - used_keyword_ids
        if orphan_ids:
            logger.warning(f"{len(orphan_ids)} orphan keywords, creating overflow group")
            orphan_keywords = [k for k in original_keywords if k.id in orphan_ids]
            
            # Create overflow groups
            chunks = [list(orphan_ids)[i:i+self.MAX_KEYWORDS_PER_GROUP] 
                     for i in range(0, len(orphan_ids), self.MAX_KEYWORDS_PER_GROUP)]
            
            for i, chunk in enumerate(chunks):
                chunk_kws = [k.keyword for k in orphan_keywords if k.id in chunk]
                overflow_group = KeywordGroupSchema(
                    name=f"Diğer Kelimeler - {i+1}" if len(chunks) > 1 else "Diğer Kelimeler",
                    theme="AI tarafından gruplandırılamayan kelimeler",
                    keyword_ids=list(chunk),
                    keywords=chunk_kws
                )
                validated.append(overflow_group)
        
        return validated
    
    def _create_single_group(
        self, 
        keywords: List[KeywordForGrouping]
    ) -> List[KeywordGroupSchema]:
        """Create a single group for few keywords."""
        if not keywords:
            return []
        
        # Use first keyword as group name base
        first_kw = keywords[0].keyword
        
        return [KeywordGroupSchema(
            name=f"{first_kw.title()} - Genel",
            theme="Tüm ilgili anahtar kelimeler",
            keyword_ids=[k.id for k in keywords],
            keywords=[k.keyword for k in keywords]
        )]
    
    def _fallback_grouping(
        self, 
        keywords: List[KeywordForGrouping]
    ) -> List[KeywordGroupSchema]:
        """
        Simple chunking fallback when AI fails.
        Groups keywords in order, 5 per group.
        """
        logger.warning("Using fallback grouping (simple chunking)")
        
        groups = []
        chunk_size = 5
        
        for i in range(0, len(keywords), chunk_size):
            chunk = keywords[i:i+chunk_size]
            
            # Name based on first keyword
            first_kw = chunk[0].keyword.title()
            
            group = KeywordGroupSchema(
                name=f"{first_kw} Grubu",
                theme="Otomatik gruplandırma",
                keyword_ids=[k.id for k in chunk],
                keywords=[k.keyword for k in chunk]
            )
            groups.append(group)
        
        return groups
