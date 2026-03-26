"""
Social Generator - Main Orchestrator.

Orchestrates the complete 3-phase social media content generation pipeline:
1. Generate categories from keywords (CategoryGenerator)
2. Generate ideas for each category (IdeaGenerator)
3. Generate content for selected ideas (ContentGenerator)
4. Save to database with atomic transactions
"""
from datetime import datetime
from typing import List, Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.generators.ai_service import AIService
from app.generators.social.category_generator import CategoryGenerator
from app.generators.social.idea_generator import IdeaGenerator
from app.generators.social.content_generator import ContentGenerator

from app.database.models import (
    SocialCategory,
    SocialIdea,
    SocialContent,
    ChannelPool,
    Keyword,
)
from app.schemas.social import (
    SocialCategoriesRequest,
    SocialIdeasRequest,
    SocialContentsRequest,
    SocialBulkRequest,
    SocialRegenerateRequest,
    SocialCategoriesResponse,
    SocialIdeasResponse,
    SocialContentsResponse,
    SocialBulkResponse,
    SocialFullResponse,
    SocialCategorySchema,
    SocialCategoryDBSchema,
    SocialIdeaSchema,
    SocialIdeaDBSchema,
    SocialContentSchema,
    SocialContentDBSchema,
    PlatformEnum,
    ContentFormatEnum,
    HookSchema,
    HookStyleEnum,
    CategoryTypeEnum,
)


class SocialGenerator:
    """
    Social Media content generation orchestrator.
    
    Usage:
    
    1. Full pipeline (automatic):
        generator = SocialGenerator(db, ai_service)
        result = generator.generate_full_pipeline(request)
    
    2. Step-by-step (user selection):
        categories = generator.generate_categories(request)
        ideas = generator.generate_ideas(request)
        contents = generator.generate_contents(request)
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service
        self.category_gen = CategoryGenerator(ai_service)
        self.idea_gen = IdeaGenerator(ai_service)
        self.content_gen = ContentGenerator(ai_service)
    
    # ==================== PHASE 1: CATEGORIES ====================
    
    def generate_categories(self, request: SocialCategoriesRequest) -> SocialCategoriesResponse:
        """
        Phase 1: Generate content categories.
        """
        # Get SOCIAL pool keywords
        keywords = self._get_social_keywords(request.scoring_run_id)
        
        if not keywords:
            logger.warning(f"No SOCIAL keywords found for scoring_run_id={request.scoring_run_id}")
            return SocialCategoriesResponse(
                scoring_run_id=request.scoring_run_id,
                total_categories=0,
                categories=[]
            )
        
        # Generate categories
        category_schemas = self.category_gen.generate(
            keywords=keywords,
            brand_name=request.brand_name,
            brand_context=request.brand_context,
            max_categories=request.max_categories
        )
        
        # Save to database
        saved_categories = self._save_categories(category_schemas, request.scoring_run_id, keywords)
        
        return SocialCategoriesResponse(
            scoring_run_id=request.scoring_run_id,
            total_categories=len(saved_categories),
            categories=saved_categories
        )
    
    # ==================== PHASE 2: IDEAS ====================
    
    def generate_ideas(self, request: SocialIdeasRequest, brand_name: str) -> List[SocialIdeasResponse]:
        """
        Phase 2: Generate ideas for selected categories.
        """
        responses = []
        
        for category_id in request.category_ids:
            # Get category from DB
            db_category = self.db.query(SocialCategory).filter(
                SocialCategory.id == category_id
            ).first()
            
            if not db_category:
                logger.warning(f"Category {category_id} not found")
                continue
            
            # Convert to schema
            category = SocialCategorySchema(
                id=db_category.id,
                category_name=db_category.category_name,
                category_type=CategoryTypeEnum(db_category.category_type or "educational"),
                description=db_category.description or "",
                relevance_score=db_category.relevance_score or 0.0,
                suggested_keywords=[]
            )
            
            # Get keywords for this category
            if db_category.suggested_keyword_ids:
                keywords_query = self.db.query(Keyword).filter(
                    Keyword.id.in_(db_category.suggested_keyword_ids)
                ).all()
                category.suggested_keywords = [k.keyword for k in keywords_query]
            
            # Generate ideas
            target_platforms = request.target_platforms
            idea_schemas = self.idea_gen.generate(
                category=category,
                brand_name=brand_name,
                ideas_per_category=request.ideas_per_category,
                target_platforms=target_platforms
            )
            
            # Save to database
            saved_ideas = self._save_ideas(idea_schemas, category_id)
            
            responses.append(SocialIdeasResponse(
                category_id=category_id,
                category_name=db_category.category_name,
                total_ideas=len(saved_ideas),
                ideas=saved_ideas
            ))
        
        return responses
    
    # ==================== PHASE 3: CONTENTS ====================
    
    def generate_contents(self, request: SocialContentsRequest) -> SocialContentsResponse:
        """
        Phase 3: Generate contents for selected ideas.
        """
        contents = []
        
        for idea_id in request.idea_ids:
            # Get idea from DB
            db_idea = self.db.query(SocialIdea).filter(SocialIdea.id == idea_id).first()
            
            if not db_idea:
                logger.warning(f"Idea {idea_id} not found")
                continue
            
            # Convert to schema
            idea = SocialIdeaSchema(
                id=db_idea.id,
                category_id=db_idea.category_id,
                idea_title=db_idea.idea_title,
                idea_description=db_idea.idea_description or "",
                target_platform=PlatformEnum(db_idea.target_platform or "instagram"),
                content_format=ContentFormatEnum(db_idea.content_format or "post"),
                trend_alignment=db_idea.trend_alignment or 0.0,
                related_keyword=None,
                is_selected=db_idea.is_selected
            )
            
            # Generate content
            content_schema = self.content_gen.generate(
                idea=idea,
                brand_name=request.brand_name,
                brand_tone=request.brand_tone
            )
            
            if content_schema:
                # Save to database
                saved_content = self._save_content(content_schema, idea_id)
                if saved_content:
                    contents.append(saved_content)
        
        return SocialContentsResponse(
            idea_ids=request.idea_ids,
            total_contents=len(contents),
            contents=contents
        )
    
    # ==================== FULL PIPELINE ====================
    
    def generate_full_pipeline(self, request: SocialBulkRequest) -> SocialBulkResponse:
        """
        Full 3-phase pipeline with automatic idea selection.
        """
        # Phase 1: Categories
        cat_request = SocialCategoriesRequest(
            scoring_run_id=request.scoring_run_id,
            brand_name=request.brand_name,
            brand_context=request.brand_context
        )
        cat_response = self.generate_categories(cat_request)
        
        if not cat_response.categories:
            return self._empty_response(request.scoring_run_id)
        
        # Phase 2: Ideas for all categories
        category_ids = [c.id for c in cat_response.categories]
        idea_request = SocialIdeasRequest(category_ids=category_ids)
        idea_responses = self.generate_ideas(idea_request, request.brand_name)
        
        # Collect all ideas
        all_ideas = []
        for resp in idea_responses:
            all_ideas.extend(resp.ideas)
        
        # Auto-select ideas by trend_alignment threshold
        selected_ideas = [
            idea for idea in all_ideas 
            if idea.trend_alignment >= request.auto_select_threshold
        ]
        
        # If not enough, take top by trend_alignment
        if len(selected_ideas) < request.max_contents:
            sorted_ideas = sorted(all_ideas, key=lambda x: x.trend_alignment, reverse=True)
            selected_ideas = sorted_ideas[:request.max_contents]
        else:
            selected_ideas = selected_ideas[:request.max_contents]
        
        # Mark selected in DB
        for idea in selected_ideas:
            self.db.query(SocialIdea).filter(SocialIdea.id == idea.id).update(
                {"is_selected": True}
            )
        self.db.commit()
        
        # Phase 3: Contents for selected ideas
        content_request = SocialContentsRequest(
            idea_ids=[i.id for i in selected_ideas],
            brand_name=request.brand_name
        )
        content_response = self.generate_contents(content_request)
        
        return SocialBulkResponse(
            scoring_run_id=request.scoring_run_id,
            total_categories=len(cat_response.categories),
            total_ideas=len(all_ideas),
            total_contents=len(content_response.contents),
            categories=cat_response.categories,
            selected_ideas=selected_ideas,
            contents=content_response.contents,
            generated_at=datetime.utcnow()
        )
    
    # ==================== REGENERATE ====================
    
    def regenerate_idea(
        self, 
        idea_id: int, 
        brand_name: str,
        additional_context: Optional[str] = None
    ) -> Optional[SocialIdeaDBSchema]:
        """Regenerate a single idea."""
        db_idea = self.db.query(SocialIdea).filter(SocialIdea.id == idea_id).first()
        if not db_idea:
            return None
        
        db_category = self.db.query(SocialCategory).filter(
            SocialCategory.id == db_idea.category_id
        ).first()
        
        if not db_category:
            return None
        
        # Create old idea schema
        old_idea = SocialIdeaSchema(
            id=db_idea.id,
            category_id=db_idea.category_id,
            idea_title=db_idea.idea_title,
            idea_description=db_idea.idea_description or "",
            target_platform=PlatformEnum(db_idea.target_platform or "instagram"),
            content_format=ContentFormatEnum(db_idea.content_format or "post"),
            trend_alignment=db_idea.trend_alignment or 0.0,
            is_selected=db_idea.is_selected
        )
        
        # Create category schema
        category = SocialCategorySchema(
            id=db_category.id,
            category_name=db_category.category_name,
            category_type=CategoryTypeEnum(db_category.category_type or "educational"),
            description=db_category.description or "",
            relevance_score=db_category.relevance_score or 0.0
        )
        
        # Regenerate
        new_idea = self.idea_gen.regenerate(
            old_idea=old_idea,
            category=category,
            brand_name=brand_name,
            additional_context=additional_context
        )
        
        if new_idea:
            # Update in DB
            db_idea.idea_title = new_idea.idea_title
            db_idea.idea_description = new_idea.idea_description
            db_idea.target_platform = new_idea.target_platform.value
            db_idea.content_format = new_idea.content_format.value
            db_idea.trend_alignment = new_idea.trend_alignment
            db_idea.regeneration_count = (db_idea.regeneration_count or 0) + 1
            
            self.db.commit()
            self.db.refresh(db_idea)
            
            return self._idea_to_schema(db_idea)
        
        return None
    
    def regenerate_content(
        self,
        content_id: int,
        brand_name: str,
        additional_context: Optional[str] = None
    ) -> Optional[SocialContentDBSchema]:
        """Regenerate content for an idea."""
        db_content = self.db.query(SocialContent).filter(SocialContent.id == content_id).first()
        if not db_content:
            return None
        
        db_idea = self.db.query(SocialIdea).filter(SocialIdea.id == db_content.idea_id).first()
        if not db_idea:
            return None
        
        # Create schemas
        old_content = self._content_to_schema(db_content)
        idea = SocialIdeaSchema(
            id=db_idea.id,
            category_id=db_idea.category_id,
            idea_title=db_idea.idea_title,
            idea_description=db_idea.idea_description or "",
            target_platform=PlatformEnum(db_idea.target_platform or "instagram"),
            content_format=ContentFormatEnum(db_idea.content_format or "post"),
            trend_alignment=db_idea.trend_alignment or 0.0,
            is_selected=db_idea.is_selected
        )
        
        # Regenerate
        new_content = self.content_gen.regenerate(
            old_content=old_content,
            idea=idea,
            brand_name=brand_name,
            additional_context=additional_context
        )
        
        if new_content:
            # Update in DB
            db_content.hooks = [{"text": h.text, "style": h.style.value, "ab_score": h.ab_score} for h in new_content.hooks]
            db_content.caption = new_content.caption
            db_content.scenario = new_content.scenario
            db_content.visual_suggestion = new_content.visual_suggestion
            db_content.video_concept = new_content.video_concept
            db_content.cta_text = new_content.cta_text
            db_content.hashtags = new_content.hashtags
            db_content.industry_posting_suggestion = new_content.industry_posting_suggestion
            db_content.platform_notes = new_content.platform_notes
            db_content.regeneration_count = (db_content.regeneration_count or 0) + 1
            
            self.db.commit()
            self.db.refresh(db_content)
            
            return self._content_to_schema(db_content)
        
        return None
    
    # ==================== GET METHODS ====================
    
    def get_all(self, scoring_run_id: int) -> SocialFullResponse:
        """Get all social media data for a scoring run."""
        categories = self.db.query(SocialCategory).filter(
            SocialCategory.scoring_run_id == scoring_run_id
        ).all()
        
        cat_schemas = [self._category_to_schema(c) for c in categories]
        
        category_ids = [c.id for c in categories]
        ideas = self.db.query(SocialIdea).filter(
            SocialIdea.category_id.in_(category_ids)
        ).all() if category_ids else []
        
        idea_schemas = [self._idea_to_schema(i) for i in ideas]
        
        idea_ids = [i.id for i in ideas]
        contents = self.db.query(SocialContent).filter(
            SocialContent.idea_id.in_(idea_ids)
        ).all() if idea_ids else []
        
        content_schemas = [self._content_to_schema(c) for c in contents]
        
        return SocialFullResponse(
            scoring_run_id=scoring_run_id,
            categories=cat_schemas,
            ideas=idea_schemas,
            contents=content_schemas
        )
    
    def select_idea(self, idea_id: int, selected: bool = True) -> bool:
        """Select or deselect an idea."""
        result = self.db.query(SocialIdea).filter(SocialIdea.id == idea_id).update(
            {"is_selected": selected}
        )
        self.db.commit()
        return result > 0
    
    # ==================== PRIVATE METHODS ====================
    
    def _get_social_keywords(self, scoring_run_id: int) -> List[dict]:
        """Get keywords from SOCIAL pool (pre-filter enriched)."""
        pool_entries = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == "SOCIAL"
        ).all()
        
        if not pool_entries:
            return []
        
        keyword_ids = [entry.keyword_id for entry in pool_entries]
        keywords = self.db.query(Keyword).filter(Keyword.id.in_(keyword_ids)).all()
        
        keyword_list = [{"id": k.id, "keyword": k.keyword} for k in keywords]
        
        # Pre-filter enrichment
        try:
            from app.core.channel.pre_filters.enricher import PreFilterEnricher
            enricher = PreFilterEnricher(self.db)
            keyword_list = enricher.enrich_keywords(scoring_run_id, "SOCIAL", keyword_list)
        except Exception:
            pass  # Fail-open
        
        return keyword_list
    
    def _save_categories(
        self, 
        categories: List[SocialCategorySchema],
        scoring_run_id: int,
        keywords: List[dict]
    ) -> List[SocialCategoryDBSchema]:
        """Save categories to database."""
        saved = []
        keyword_map = {k["keyword"]: k["id"] for k in keywords}
        
        try:
            for cat in categories:
                # Map keyword names to IDs
                keyword_ids = [
                    keyword_map[kw] for kw in cat.suggested_keywords 
                    if kw in keyword_map
                ]
                
                db_cat = SocialCategory(
                    scoring_run_id=scoring_run_id,
                    category_name=cat.category_name,
                    category_type=cat.category_type.value,
                    description=cat.description,
                    relevance_score=cat.relevance_score,
                    suggested_keyword_ids=keyword_ids
                )
                self.db.add(db_cat)
                self.db.flush()
                
                saved.append(self._category_to_schema(db_cat))
            
            self.db.commit()
            return saved
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to save categories: {e}")
            raise
    
    def _save_ideas(
        self,
        ideas: List[SocialIdeaSchema],
        category_id: int
    ) -> List[SocialIdeaDBSchema]:
        """Save ideas to database."""
        saved = []
        
        try:
            for idea in ideas:
                db_idea = SocialIdea(
                    category_id=category_id,
                    idea_title=idea.idea_title,
                    idea_description=idea.idea_description,
                    target_platform=idea.target_platform.value,
                    content_format=idea.content_format.value,
                    trend_alignment=idea.trend_alignment,
                    is_selected=False,
                    regeneration_count=0
                )
                self.db.add(db_idea)
                self.db.flush()
                
                saved.append(self._idea_to_schema(db_idea))
            
            self.db.commit()
            return saved
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to save ideas: {e}")
            raise
    
    def _save_content(
        self,
        content: SocialContentSchema,
        idea_id: int
    ) -> Optional[SocialContentDBSchema]:
        """Save content to database."""
        try:
            # Convert hooks to JSON format
            hooks_json = [
                {"text": h.text, "style": h.style.value, "ab_score": h.ab_score}
                for h in content.hooks
            ]
            
            db_content = SocialContent(
                idea_id=idea_id,
                hooks=hooks_json,
                caption=content.caption,
                scenario=content.scenario,
                visual_suggestion=content.visual_suggestion,
                video_concept=content.video_concept,
                cta_text=content.cta_text,
                hashtags=content.hashtags,
                industry_posting_suggestion=content.industry_posting_suggestion,
                platform_notes=content.platform_notes,
                regeneration_count=0
            )
            self.db.add(db_content)
            self.db.commit()
            self.db.refresh(db_content)
            
            return self._content_to_schema(db_content)
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to save content: {e}")
            return None
    
    def _category_to_schema(self, cat: SocialCategory) -> SocialCategoryDBSchema:
        """Convert DB model to schema."""
        return SocialCategoryDBSchema(
            id=cat.id,
            scoring_run_id=cat.scoring_run_id,
            category_name=cat.category_name,
            category_type=CategoryTypeEnum(cat.category_type or "educational"),
            description=cat.description or "",
            relevance_score=cat.relevance_score or 0.0,
            suggested_keywords=[],
            suggested_keyword_ids=cat.suggested_keyword_ids,
            created_at=cat.created_at
        )
    
    def _idea_to_schema(self, idea: SocialIdea) -> SocialIdeaDBSchema:
        """Convert DB model to schema."""
        return SocialIdeaDBSchema(
            id=idea.id,
            category_id=idea.category_id,
            keyword_id=idea.keyword_id,
            idea_title=idea.idea_title,
            idea_description=idea.idea_description or "",
            target_platform=PlatformEnum(idea.target_platform or "instagram"),
            content_format=ContentFormatEnum(idea.content_format or "post"),
            trend_alignment=idea.trend_alignment or 0.0,
            is_selected=idea.is_selected,
            regeneration_count=idea.regeneration_count or 0,
            created_at=idea.created_at
        )
    
    def _content_to_schema(self, content: SocialContent) -> SocialContentDBSchema:
        """Convert DB model to schema."""
        # Parse hooks from JSON
        hooks = []
        if content.hooks:
            for h in content.hooks:
                style = h.get("style", "question")
                if style not in [s.value for s in HookStyleEnum]:
                    style = "question"
                hooks.append(HookSchema(
                    text=h.get("text", ""),
                    style=HookStyleEnum(style),
                    ab_score=h.get("ab_score")
                ))
        
        return SocialContentDBSchema(
            id=content.id,
            idea_id=content.idea_id,
            content_output_id=content.content_output_id,
            hooks=hooks,
            caption=content.caption,
            scenario=content.scenario,
            visual_suggestion=content.visual_suggestion,
            video_concept=content.video_concept,
            cta_text=content.cta_text or "",
            hashtags=content.hashtags or [],
            industry_posting_suggestion=content.industry_posting_suggestion,
            platform_notes=content.platform_notes,
            regeneration_count=content.regeneration_count or 0,
            created_at=content.created_at
        )
    
    def _empty_response(self, scoring_run_id: int) -> SocialBulkResponse:
        """Return empty response."""
        return SocialBulkResponse(
            scoring_run_id=scoring_run_id,
            total_categories=0,
            total_ideas=0,
            total_contents=0,
            categories=[],
            selected_ideas=[],
            contents=[],
            generated_at=datetime.utcnow()
        )
