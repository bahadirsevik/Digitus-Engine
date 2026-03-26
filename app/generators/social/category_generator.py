"""
Category Generator for Social Media.
Phase 1: Generates content categories from keywords and brand context.
"""
import json
from typing import List, Optional
from loguru import logger

from app.generators.ai_service import AIService
from app.generators.social.prompt_templates import SOCIAL_CATEGORY_PROMPT
from app.schemas.social import SocialCategorySchema, CategoryTypeEnum


class CategoryGenerator:
    """
    Phase 1: Category Generator.
    
    Category Types:
    - educational: Bilgilendirici, öğretici
    - product_benefit: Ürün/hizmet faydası
    - social_proof: Müşteri hikayesi, case study
    - brand_story: Perde arkası, marka hikayesi
    - community: Challenge, anket, Q&A
    - trending: Trend format, viral içerik
    """
    
    MIN_CATEGORIES = 4
    MAX_CATEGORIES = 10
    
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
    
    def generate(
        self,
        keywords: List[dict],
        brand_name: str,
        brand_context: Optional[str] = None,
        max_categories: int = 6
    ) -> List[SocialCategorySchema]:
        """
        Generate content categories for given keywords.
        
        Args:
            keywords: List of keyword dicts with id and keyword
            brand_name: Brand name for personalization
            brand_context: Additional brand context
            max_categories: Maximum categories to generate
        
        Returns:
            List of SocialCategorySchema
        """
        if not keywords:
            logger.warning("No keywords provided for category generation")
            return []
        
        brand_name = (brand_name or "Marka").strip() or "Marka"
        brand_context = (brand_context or "Belirtilmedi").strip() or "Belirtilmedi"

        keywords_json = json.dumps(
            [{"id": k["id"], "keyword": k["keyword"]} for k in keywords],
            ensure_ascii=False,
            indent=2
        )

        prompt = SOCIAL_CATEGORY_PROMPT.format(
            brand_name=brand_name,
            brand_context=brand_context,
            keywords_json=keywords_json
        )
        
        try:
            response = self.ai_service.complete_json(prompt)
            categories = self._parse_response(response)
            
            # Limit to max_categories
            categories = categories[:max_categories]
            
            logger.info(f"Generated {len(categories)} categories for {brand_name}")
            return categories
            
        except Exception as e:
            logger.error(f"Category generation failed: {e}")
            # Fallback: create default categories
            return self._fallback_categories(keywords)
    
    def _parse_response(self, response: str) -> List[SocialCategorySchema]:
        """Parse AI response into category schemas."""
        try:
            data = json.loads(response)
            categories = []
            
            for cat_data in data.get("categories", []):
                # Validate category_type
                cat_type = cat_data.get("category_type", "educational")
                if cat_type not in [e.value for e in CategoryTypeEnum]:
                    cat_type = "educational"
                
                category = SocialCategorySchema(
                    category_name=cat_data.get("category_name", "Unnamed Category"),
                    category_type=CategoryTypeEnum(cat_type),
                    description=cat_data.get("description", ""),
                    relevance_score=min(1.0, max(0.0, cat_data.get("relevance_score", 0.5))),
                    suggested_keywords=cat_data.get("suggested_keywords", [])
                )
                categories.append(category)
            
            return categories
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse category response: {e}")
            raise
    
    def _fallback_categories(self, keywords: List[dict]) -> List[SocialCategorySchema]:
        """Create default categories when AI fails."""
        logger.warning("Using fallback categories")
        
        default_categories = [
            SocialCategorySchema(
                category_name="Eğitici İçerikler",
                category_type=CategoryTypeEnum.EDUCATIONAL,
                description="Kullanıcıları bilgilendiren içerikler",
                relevance_score=0.7,
                suggested_keywords=[k["keyword"] for k in keywords[:3]]
            ),
            SocialCategorySchema(
                category_name="Ürün Faydaları",
                category_type=CategoryTypeEnum.PRODUCT_BENEFIT,
                description="Ürün ve hizmet avantajlarını anlatan içerikler",
                relevance_score=0.6,
                suggested_keywords=[k["keyword"] for k in keywords[3:6]]
            ),
            SocialCategorySchema(
                category_name="Trend İçerikler",
                category_type=CategoryTypeEnum.TRENDING,
                description="Güncel trendlere uygun viral formatlar",
                relevance_score=0.5,
                suggested_keywords=[k["keyword"] for k in keywords[6:9]]
            )
        ]
        
        return default_categories
