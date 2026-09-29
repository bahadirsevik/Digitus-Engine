"""
Idea Generator for Social Media.
Phase 2: Generates content ideas for each category.
"""
import json
from typing import List, Optional
from loguru import logger

from app.core.channel.ai_json import (
    AIJsonParseError,
    parse_ai_json_list,
    parse_ai_json_object,
)
from app.generators.ai_service import AIService, scoped
from app.generators.social.prompt_templates import SOCIAL_IDEA_PROMPT, IDEA_REGENERATE_PROMPT
from app.schemas.social import (
    SocialIdeaSchema,
    SocialCategorySchema,
    PlatformEnum,
    ContentFormatEnum,
)


class IdeaGenerator:
    """
    Phase 2: Idea Generator.
    
    Generates content ideas with:
    - Idea title and description
    - Target platform (instagram, tiktok, twitter, linkedin, youtube)
    - Content format (reels, carousel, story, post, thread, short)
    - Trend alignment score (0-1) - NOT viral potential guarantee
    """
    
    # Trend alignment factors (for reference, AI evaluates)
    TREND_FACTORS = {
        "trend_format_uyumu": 0.25,
        "duygusal_tetik": 0.20,
        "paylasılabilirlik": 0.20,
        "basitlik": 0.15,
        "ozgunluk": 0.10,
        "marka_uyumu": 0.10
    }
    
    MIN_IDEAS = 3
    MAX_IDEAS = 10
    
    def __init__(self, ai_service: AIService):
        self.ai_service = scoped(ai_service, "social_idea")
    
    def generate(
        self,
        category: SocialCategorySchema,
        brand_name: str,
        ideas_per_category: int = 5,
        target_platforms: Optional[List[PlatformEnum]] = None,
        excluded_topics: str = "—",
    ) -> List[SocialIdeaSchema]:
        """
        Generate ideas for a category.
        
        Args:
            category: Category to generate ideas for
            brand_name: Brand name
            ideas_per_category: Number of ideas to generate
            target_platforms: Filter to specific platforms (optional)
        
        Returns:
            List of SocialIdeaSchema
        """
        brand_name = (brand_name or "Marka").strip() or "Marka"
        ideas_count = min(max(ideas_per_category, self.MIN_IDEAS), self.MAX_IDEAS)

        prompt = SOCIAL_IDEA_PROMPT.format(
            category_name=category.category_name,
            category_type=category.category_type.value,
            category_description=category.description,
            keywords=", ".join(category.suggested_keywords) if category.suggested_keywords else "genel",
            brand_name=brand_name,
            excluded_topics=excluded_topics,
            ideas_count=ideas_count
        )
        
        try:
            # Dayanikli parse tutmazsa BIR kez yeniden uret (SEO deseni)
            response = self.ai_service.complete_json(prompt)
            try:
                ideas = self._parse_response(response, category.id or 0)
            except (ValueError, AIJsonParseError) as first_error:
                logger.warning(
                    f"Fikir JSON parse edilemedi, yeniden deneniyor: {first_error}"
                )
                response = self.ai_service.complete_json(prompt)
                ideas = self._parse_response(response, category.id or 0)

            # Filter by platform if specified
            if target_platforms:
                platform_values = [p.value for p in target_platforms]
                ideas = [i for i in ideas if i.target_platform.value in platform_values]
            
            logger.info(f"Generated {len(ideas)} ideas for category '{category.category_name}'")
            return ideas
            
        except Exception as e:
            logger.error(f"Idea generation failed: {e}")
            return self._fallback_ideas(category)
    
    def regenerate(
        self,
        old_idea: SocialIdeaSchema,
        category: SocialCategorySchema,
        brand_name: str,
        additional_context: Optional[str] = None,
        excluded_topics: str = "—",
    ) -> Optional[SocialIdeaSchema]:
        """
        Regenerate a single idea when user didn't like the previous one.
        """
        prompt = IDEA_REGENERATE_PROMPT.format(
            old_idea_title=old_idea.idea_title,
            old_platform=old_idea.target_platform.value,
            old_format=old_idea.content_format.value,
            category_name=category.category_name,
            category_type=category.category_type.value,
            keywords=", ".join(category.suggested_keywords) if category.suggested_keywords else "genel",
            brand_name=brand_name,
            excluded_topics=excluded_topics,
            additional_context=additional_context or "Daha yaratıcı ve farklı bir yaklaşım"
        )
        
        try:
            response = self.ai_service.complete_json(prompt)
            # Tek obje kurtarma: zorunlu alan yoksa hata (fail-open: None doner)
            data = parse_ai_json_object(response, ("idea_title",))

            # Validate platform
            platform = data.get("target_platform", "instagram")
            if platform not in [p.value for p in PlatformEnum]:
                platform = "instagram"
            
            # Validate format
            content_format = data.get("content_format", "post")
            if content_format not in [f.value for f in ContentFormatEnum]:
                content_format = "post"
            
            idea = SocialIdeaSchema(
                category_id=old_idea.category_id,
                idea_title=data.get("idea_title", "Regenerated Idea"),
                idea_description=data.get("idea_description", ""),
                target_platform=PlatformEnum(platform),
                content_format=ContentFormatEnum(content_format),
                trend_alignment=min(1.0, max(0.0, data.get("trend_alignment", 0.5))),
                related_keyword=data.get("related_keyword"),
                is_selected=False
            )
            
            logger.info(f"Regenerated idea: {idea.idea_title}")
            return idea
            
        except Exception as e:
            logger.error(f"Idea regeneration failed: {e}")
            return None
    
    def _parse_response(self, response: str, category_id: int) -> List[SocialIdeaSchema]:
        """Parse AI response into idea schemas."""
        try:
            # Kurtarma zinciri: truncation'da bile tam fikir objelerini cikarir
            idea_items = parse_ai_json_list(response, result_keys=("ideas",))
            if not idea_items:
                raise AIJsonParseError("Fikir yanitinda hic fikir objesi yok")
            ideas = []

            for idea_data in idea_items:
                if not idea_data.get("idea_title"):
                    raise AIJsonParseError("Fikir yanitinda idea_title eksik")

                # Validate platform
                platform = idea_data.get("target_platform", "instagram")
                if platform not in [p.value for p in PlatformEnum]:
                    platform = "instagram"
                
                # Validate format
                content_format = idea_data.get("content_format", "post")
                if content_format not in [f.value for f in ContentFormatEnum]:
                    content_format = "post"
                
                idea = SocialIdeaSchema(
                    category_id=category_id,
                    idea_title=idea_data.get("idea_title"),
                    idea_description=idea_data.get("idea_description", ""),
                    target_platform=PlatformEnum(platform),
                    content_format=ContentFormatEnum(content_format),
                    trend_alignment=min(1.0, max(0.0, idea_data.get("trend_alignment", 0.5))),
                    related_keyword=idea_data.get("related_keyword"),
                    is_selected=False
                )
                ideas.append(idea)
            
            return ideas
            
        except (json.JSONDecodeError, AIJsonParseError) as e:
            logger.error(f"Failed to parse idea response: {e}")
            raise
    
    def _fallback_ideas(self, category: SocialCategorySchema) -> List[SocialIdeaSchema]:
        """Create default ideas when AI fails."""
        logger.warning("Using fallback ideas")
        
        return [
            SocialIdeaSchema(
                category_id=category.id or 0,
                idea_title=f"{category.category_name} - Temel İçerik",
                idea_description="Kategori hakkında genel bilgi veren içerik",
                target_platform=PlatformEnum.INSTAGRAM,
                content_format=ContentFormatEnum.POST,
                trend_alignment=0.5,
                related_keyword=category.suggested_keywords[0] if category.suggested_keywords else None,
                is_selected=False
            )
        ]
