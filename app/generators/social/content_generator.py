"""
Content Generator for Social Media.
Phase 3: Generates full content packages for selected ideas.
"""
import json
from typing import List, Optional
from loguru import logger

from app.generators.ai_service import AIService
from app.generators.social.prompt_templates import SOCIAL_CONTENT_PROMPT, CONTENT_REGENERATE_PROMPT
from app.schemas.social import (
    SocialContentSchema,
    SocialIdeaSchema,
    HookSchema,
    HookStyleEnum,
)


class ContentGenerator:
    """
    Phase 3: Content Generator.
    
    Generates complete content packages:
    - JSONB Hooks (3+, flexible, different styles)
    - Caption (platform-aware character limits)
    - Scenario/Script (for video/carousel)
    - Visual/Video suggestions
    - CTA + Hashtags (10-15)
    - Industry posting suggestion (NOT user-specific)
    - Platform notes
    """
    
    PLATFORM_LIMITS = {
        "instagram": 2200,
        "tiktok": 2200,
        "twitter": 280,
        "linkedin": 3000,
        "youtube": 5000
    }
    
    MIN_HASHTAGS = 5
    MAX_HASHTAGS = 20
    MIN_HOOKS = 1
    
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
    
    def generate(
        self,
        idea: SocialIdeaSchema,
        brand_name: str,
        brand_tone: Optional[str] = None
    ) -> Optional[SocialContentSchema]:
        """
        Generate full content package for an idea.
        
        Args:
            idea: Selected idea to generate content for
            brand_name: Brand name
            brand_tone: Brand tone (samimi, profesyonel, eğlenceli)
        
        Returns:
            SocialContentSchema with all components
        """
        brand_name = (brand_name or "Marka").strip() or "Marka"
        brand_tone = (brand_tone or "doğal ve samimi").strip() or "doğal ve samimi"

        prompt = SOCIAL_CONTENT_PROMPT.format(
            idea_title=idea.idea_title,
            idea_description=idea.idea_description,
            target_platform=idea.target_platform.value,
            content_format=idea.content_format.value,
            related_keyword=idea.related_keyword or "genel",
            brand_name=brand_name,
            brand_tone=brand_tone
        )
        
        try:
            response = self.ai_service.complete_json(prompt)
            content = self._parse_response(response, idea.id or 0)
            
            # Validate caption length
            content = self._validate_caption_length(content, idea.target_platform.value)
            
            logger.info(f"Generated content for idea: {idea.idea_title}")
            return content
            
        except Exception as e:
            logger.error(f"Content generation failed: {e}")
            return self._fallback_content(idea)
    
    def regenerate(
        self,
        old_content: SocialContentSchema,
        idea: SocialIdeaSchema,
        brand_name: str,
        additional_context: Optional[str] = None
    ) -> Optional[SocialContentSchema]:
        """
        Regenerate content when user didn't like the previous one.
        """
        old_hook = old_content.hooks[0].text if old_content.hooks else "N/A"
        old_caption_start = old_content.caption[:100] if old_content.caption else "N/A"
        
        prompt = CONTENT_REGENERATE_PROMPT.format(
            idea_title=idea.idea_title,
            target_platform=idea.target_platform.value,
            content_format=idea.content_format.value,
            old_hook=old_hook,
            old_caption_start=old_caption_start,
            brand_name=brand_name,
            additional_context=additional_context or "Daha yaratıcı ve farklı bir yaklaşım"
        )
        
        try:
            response = self.ai_service.complete_json(prompt)
            content = self._parse_response(response, idea.id or 0)
            
            logger.info(f"Regenerated content for idea: {idea.idea_title}")
            return content
            
        except Exception as e:
            logger.error(f"Content regeneration failed: {e}")
            return None
    
    def _parse_response(self, response: str, idea_id: int) -> SocialContentSchema:
        """Parse AI response into content schema."""
        try:
            data = json.loads(response)
            
            # Parse hooks (JSONB format)
            hooks = []
            for hook_data in data.get("hooks", []):
                style = hook_data.get("style", "question")
                if style not in [s.value for s in HookStyleEnum]:
                    style = "question"
                
                hook = HookSchema(
                    text=hook_data.get("text", ""),
                    style=HookStyleEnum(style),
                    ab_score=hook_data.get("ab_score")
                )
                hooks.append(hook)
            
            # Ensure at least 1 hook
            if not hooks:
                hooks = [HookSchema(text="İçeriğinizi keşfedin", style=HookStyleEnum.CURIOSITY)]
            
            # Parse hashtags
            hashtags = data.get("hashtags", [])
            if isinstance(hashtags, str):
                hashtags = [h.strip().replace("#", "") for h in hashtags.split(",")]
            hashtags = [h.replace("#", "") for h in hashtags]
            
            # Limit hashtags
            hashtags = hashtags[:self.MAX_HASHTAGS]
            if len(hashtags) < self.MIN_HASHTAGS:
                hashtags.extend(["sosyalmedya", "icerik", "trend", "viral", "takip"])
                hashtags = hashtags[:self.MAX_HASHTAGS]
            
            content = SocialContentSchema(
                idea_id=idea_id,
                hooks=hooks,
                caption=data.get("caption", ""),
                scenario=data.get("scenario"),
                visual_suggestion=data.get("visual_suggestion"),
                video_concept=data.get("video_concept"),
                cta_text=data.get("cta_text", "Kaydet ve paylaş 🔖"),
                hashtags=hashtags,
                industry_posting_suggestion=data.get("industry_posting_suggestion"),
                platform_notes=data.get("platform_notes")
            )
            
            return content
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse content response: {e}")
            raise
    
    def _validate_caption_length(self, content: SocialContentSchema, platform: str) -> SocialContentSchema:
        """Validate and truncate caption if needed."""
        limit = self.PLATFORM_LIMITS.get(platform, 2200)
        
        if len(content.caption) > limit:
            # Truncate at word boundary
            truncated = content.caption[:limit-3].rsplit(' ', 1)[0] + "..."
            content.caption = truncated
            logger.warning(f"Caption truncated to {limit} chars for {platform}")
        
        return content
    
    def _fallback_content(self, idea: SocialIdeaSchema) -> SocialContentSchema:
        """Create default content when AI fails."""
        logger.warning("Using fallback content")
        
        return SocialContentSchema(
            idea_id=idea.id or 0,
            hooks=[
                HookSchema(text="Bu içeriği kaçırmayın!", style=HookStyleEnum.CURIOSITY),
                HookSchema(text=f"{idea.idea_title} hakkında bilmeniz gerekenler", style=HookStyleEnum.QUESTION),
                HookSchema(text="Herkes bunu konuşuyor", style=HookStyleEnum.RELATABLE)
            ],
            caption=f"{idea.idea_title}\n\n{idea.idea_description}\n\nDaha fazlası için takip edin! 👇",
            scenario=None,
            visual_suggestion="Minimalist tasarım, marka renkleri",
            video_concept=None,
            cta_text="Kaydet ve paylaş 🔖",
            hashtags=["sosyalmedya", "icerik", "trend", "viral", "takip", "kesfet", "fyp", "reels", "post", "digital"],
            industry_posting_suggestion="Genel öneri: Hafta içi 18:00-21:00 arası. NOT: Bu genel endüstri standardıdır.",
            platform_notes="İlk satır dikkat çekici olmalı."
        )
