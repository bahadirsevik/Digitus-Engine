"""
Keyword Grouper for Google Ads.

Groups keywords by purchase intent for optimized ad groups.
Uses AI to semantically cluster keywords.
"""
import json
import re
from typing import List, Dict, Optional, Any
from loguru import logger

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object
from app.core.constants import ADS_KEYWORDS_PER_GROUP
from app.core.site_analyzer.turkish_normalizer import normalize_turkish
from app.generators.ai_service import AIService, scoped
from app.generators.ads.prompt_templates import ADS_GROUPING_PROMPT
from app.schemas.ads import KeywordForGrouping, KeywordGroupSchema, GroupingResponse


def _tokens(text: str) -> set:
    return set(normalize_turkish(text).split())


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _turkish_title(text: str) -> str:
    """'istanbul laptop' -> 'İstanbul Laptop' (str.title() 'Istanbul' yapar)."""
    def cap(word: str) -> str:
        first = {"i": "İ", "ı": "I"}.get(word[0], word[0].upper())
        return first + word[1:].replace("İ", "i").replace("I", "ı").lower()
    return " ".join(cap(w) for w in text.split())


class KeywordGrouper:
    """
    Groups keywords into ad groups of ~15 keywords.

    Rules:
    - G = max(1, n // 15) groups (capped by max_groups), balanced sizes
    - The pool is never cut; the AI answer is repaired to exactly G groups
    - Same product + search intent in same group
    - Descriptive group names
    """

    GROUPING_MAX_TOKENS = 8000
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
        self.ai_service = scoped(ai_service, "ads_grouping")
    
    def _target_sizes(self, n: int, max_groups: Optional[int] = None) -> List[int]:
        """Balanced group sizes (largest first); max_groups caps the group count only."""
        if n == 0:
            return []
        g = max(1, n // ADS_KEYWORDS_PER_GROUP)
        if max_groups:
            g = max(1, min(g, max_groups))
        base, extra = divmod(n, g)
        return [base + 1 if i < extra else base for i in range(g)]

    def group_keywords(
        self, 
        keywords: List[KeywordForGrouping],
        max_groups: Optional[int] = None,
    ) -> List[KeywordGroupSchema]:
        """
        Group keywords using AI.
        
        Args:
            keywords: List of keywords with id and text
            max_groups: caps the number of groups (never drops keywords)
            
        Returns:
            Exactly G KeywordGroupSchema (see _target_sizes), every keyword once
        """
        if not keywords:
            logger.warning("No keywords provided for grouping")
            return []
        
        sizes = self._target_sizes(len(keywords), max_groups)
        if len(sizes) == 1:
            return self._create_single_group(keywords)
        
        # Prepare keywords for AI
        keywords_json = json.dumps(
            [{"id": k.id, "keyword": k.keyword} for k in keywords],
            ensure_ascii=False,
            indent=2
        )
        
        # Generate groups via AI
        prompt = ADS_GROUPING_PROMPT.format(
            keywords_json=keywords_json,
            keyword_count=len(keywords),
            group_count=len(sizes),
            group_sizes=", ".join(map(str, sizes)),
        )

        try:
            validated_groups = self._generate_and_validate_groups(
                prompt=prompt,
                keywords=keywords,
                sizes=sizes,
                stage="primary"
            )
            logger.info(f"Created {len(validated_groups)} ad groups from {len(keywords)} keywords")
            return validated_groups
        except Exception as e:
            logger.warning(f"AI grouping primary attempt failed: {e}")

        strict_prompt = self._build_strict_retry_prompt(keywords_json, sizes)
        try:
            validated_groups = self._generate_and_validate_groups(
                prompt=strict_prompt,
                keywords=keywords,
                sizes=sizes,
                stage="retry"
            )
            logger.info(f"Created {len(validated_groups)} ad groups from {len(keywords)} keywords (retry)")
            return validated_groups
        except Exception as e:
            logger.error(f"AI grouping retry failed: {e}")
            return self._fallback_grouping(keywords, sizes)

    def _generate_and_validate_groups(
        self,
        prompt: str,
        keywords: List[KeywordForGrouping],
        sizes: List[int],
        stage: str
    ) -> List[KeywordGroupSchema]:
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=self.GROUPING_MAX_TOKENS,
            response_schema=self.GROUPING_RESPONSE_SCHEMA
        )
        groups = self._parse_grouping_response(response, keywords, stage=stage)
        return self._repair_groups(groups, keywords, sizes)

    def _build_strict_retry_prompt(self, keywords_json: str, sizes: List[int]) -> str:
        return (
            f"Aşağıdaki anahtar kelimeleri TAM {len(sizes)} reklam grubuna ayır; "
            f"grupların kelime sayıları tam olarak şunlar olsun: {', '.join(map(str, sizes))}.\n"
            "Her kelime TAM BİR grupta yer alsın.\n"
            "Önce aynı ürün + aynı arama niyetindeki kelimeleri birlikte tut; "
            "küçük temaları en yakın temayla birleştir, gerekirse grup adını/temasını genişlet.\n"
            "Grup adı ve teması gruptaki TÜM kelimeleri kapsamalı: hedef boyuta ulaşmak için farklı "
            "hizmetler birleştirildiyse adı ve temayı buna göre genişlet "
            "(örn: \"SEO ve Web Tasarım Hizmetleri\").\n"
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
            # Kurtarma zinciri (fence/trailing virgul/dengesiz parantez);
            # 'ad_groups' tasimayan obje "kurtarilmis" sayilmaz
            data = parse_ai_json_object(response, ("ad_groups",))
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
    
    def _repair_groups(
        self,
        groups: List[KeywordGroupSchema],
        keywords: List[KeywordForGrouping],
        sizes: List[int]
    ) -> List[KeywordGroupSchema]:
        """
        Deterministically turn any AI answer into exactly len(sizes) groups of
        exactly those sizes, every keyword once. Keywords the repair places or
        moves are listed in the receiving group's fill_keyword_ids.
        """
        by_id = {k.id: k for k in keywords}
        order = {k.id: i for i, k in enumerate(keywords)}
        tokens = {k.id: _tokens(k.keyword) for k in keywords}

        # Unknown ids dropped; a duplicated id stays at its first occurrence
        seen = set()
        buckets = []
        for g in groups:
            ids = []
            for kid in g.keyword_ids:
                if kid in by_id and kid not in seen:
                    seen.add(kid)
                    ids.append(kid)
            if ids:
                buckets.append({"name": g.name, "theme": g.theme, "ids": ids, "fill": set()})
        if len(seen) < len(sizes):
            raise ValueError(f"AI grouping placed only {len(seen)} known keywords for {len(sizes)} groups")
        unplaced = [k.id for k in keywords if k.id not in seen]

        # Too many groups: keep the G largest (tie: AI order), dissolve the rest
        if len(buckets) > len(sizes):
            keep = set(sorted(range(len(buckets)), key=lambda i: (-len(buckets[i]["ids"]), i))[:len(sizes)])
            dissolved = [kid for i, b in enumerate(buckets) if i not in keep for kid in b["ids"]]
            buckets = [b for i, b in enumerate(buckets) if i in keep]
            unplaced = sorted(unplaced + dissolved, key=order.get)
        # Too few groups: split the largest (tie: lowest index); placed ids >= G guarantees size >= 2
        while len(buckets) < len(sizes):
            big = max(range(len(buckets)), key=lambda i: (len(buckets[i]["ids"]), -i))
            b = buckets[big]
            half = (len(b["ids"]) + 1) // 2
            moved = b["ids"][half:]
            # Split-off keywords are repair moves too (not where the AI put them)
            buckets.append({"name": f"{b['name']} 2", "theme": b["theme"], "ids": moved, "fill": set(moved)})
            b["ids"] = b["ids"][:half]

        # Biggest bucket gets the biggest target (sizes are sorted descending)
        ranked = sorted(range(len(buckets)), key=lambda i: (-len(buckets[i]["ids"]), i))
        for i, size in zip(ranked, sizes):
            buckets[i]["target"] = size

        def retoken(b: dict) -> None:
            b["tokens"] = set().union(*(tokens[kid] for kid in b["ids"]))

        def room(b: dict) -> int:
            return b["target"] - len(b["ids"])

        def receive(b: dict, kid: int) -> None:
            b["ids"].append(kid)
            b["fill"].add(kid)
            retoken(b)

        for b in buckets:
            retoken(b)

        # Unplaced keywords go to the bucket with free room that overlaps most
        for kid in unplaced:
            ri = min(
                (i for i, b in enumerate(buckets) if room(b) > 0),
                key=lambda i: (-_jaccard(tokens[kid], buckets[i]["tokens"]), i),
            )
            receive(buckets[ri], kid)

        # Oversized buckets hand keywords to undersized ones, best overlap first
        while True:
            pairs = [
                (-_jaccard(tokens[kid], r["tokens"]), ri, order[kid], bi, kid)
                for bi, b in enumerate(buckets) if room(b) < 0
                for kid in b["ids"]
                for ri, r in enumerate(buckets) if room(r) > 0
            ]
            if not pairs:
                break
            _, ri, _, bi, kid = min(pairs)
            buckets[bi]["ids"].remove(kid)
            retoken(buckets[bi])
            receive(buckets[ri], kid)

        filled = sum(len(b["fill"]) for b in buckets)
        if filled:
            logger.warning(f"AI grouping repaired: {filled} keywords placed/moved")
        return [
            KeywordGroupSchema(
                name=b["name"],
                theme=b["theme"],
                keyword_ids=b["ids"],
                keywords=[by_id[kid].keyword for kid in b["ids"]],
                fill_keyword_ids=[kid for kid in b["ids"] if kid in b["fill"]],
            )
            for b in buckets
        ]
    
    def _create_single_group(
        self, 
        keywords: List[KeywordForGrouping]
    ) -> List[KeywordGroupSchema]:
        """Create a single group holding every keyword (G == 1)."""
        if not keywords:
            return []
        
        # Use first keyword as group name base
        first_kw = keywords[0].keyword
        
        return [KeywordGroupSchema(
            name=f"{_turkish_title(first_kw)} - Genel",
            theme="Tüm ilgili anahtar kelimeler",
            keyword_ids=[k.id for k in keywords],
            keywords=[k.keyword for k in keywords]
        )]
    
    def _fallback_grouping(
        self, 
        keywords: List[KeywordForGrouping],
        sizes: List[int]
    ) -> List[KeywordGroupSchema]:
        """
        Simple chunking fallback when AI fails.
        Groups keywords in input order into the same target sizes.
        """
        logger.warning("Using fallback grouping (simple chunking)")
        
        groups = []
        start = 0
        
        for size in sizes:
            chunk = keywords[start:start + size]
            start += size
            
            # Name based on first keyword
            first_kw = _turkish_title(chunk[0].keyword)
            
            group = KeywordGroupSchema(
                name=f"{first_kw} Grubu",
                theme="Otomatik gruplandırma",
                keyword_ids=[k.id for k in chunk],
                keywords=[k.keyword for k in chunk]
            )
            groups.append(group)
        
        return groups
