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
    SocialContentWarning,
    PolicyWarning,
)
from app.generators.ads.validators import find_ungrounded_claims


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
        
        # Reassignment yarisi korumasi: uretim basinda versiyon snapshot'i
        assignment_snapshot = self._assignment_version(request.scoring_run_id)

        # Topic policy (plan B): prompt yönlendirmesi + deterministik ağ
        topic_terms, excluded_block = self._topic_policy_context(request.scoring_run_id)

        # Generate categories
        category_schemas = self.category_gen.generate(
            keywords=keywords,
            brand_name=request.brand_name,
            brand_context=request.brand_context,
            max_categories=request.max_categories,
            excluded_topics=excluded_block,
        )

        # Deterministik topic engeli: eşleşen kategori KAYDEDİLMEZ
        policy_warnings: List[PolicyWarning] = []
        allowed_schemas = []
        for cat in category_schemas:
            matched = self._match_topic(
                f"{cat.category_name} {cat.description}", topic_terms
            )
            if matched:
                logger.warning(
                    f"Kategori topic-policy reddi: '{cat.category_name}' "
                    f"(matched_term={matched})"
                )
                policy_warnings.append(PolicyWarning(
                    entity_type="category",
                    entity_id=None,
                    client_ref=cat.category_name,
                    display_name=cat.category_name,
                    reason_code="excluded_topic",
                    matched_term=matched,
                ))
                continue
            allowed_schemas.append(cat)

        # Kayit oncesi reassignment dogrulamasi (Codex A+B-1): AI sirasinda
        # kanal atamasi yenilendiyse cikti KAYDEDILMEZ — eski havuz urunu
        if self._assignment_changed(request.scoring_run_id, assignment_snapshot):
            logger.warning(
                f"Kategori uretimi reddedildi: run {request.scoring_run_id} "
                f"uretim sirasinda yeniden atandi"
            )
            return SocialCategoriesResponse(
                scoring_run_id=request.scoring_run_id,
                total_categories=0,
                categories=[],
                policy_warnings=[*policy_warnings, PolicyWarning(
                    entity_type="category",
                    display_name="(tum kategoriler)",
                    reason_code="assignment_changed",
                )],
            )

        # Save to database
        saved_categories = self._save_categories(allowed_schemas, request.scoring_run_id, keywords)

        return SocialCategoriesResponse(
            scoring_run_id=request.scoring_run_id,
            total_categories=len(saved_categories),
            categories=saved_categories,
            policy_warnings=policy_warnings,
        )
    
    # ==================== PHASE 2: IDEAS ====================
    
    def generate_ideas(self, request: SocialIdeasRequest, brand_name: str) -> List[SocialIdeasResponse]:
        """
        Phase 2: Generate ideas for selected categories.

        Brief sinirlari (plan_social_brief_akisi.md §7/§8): brief'e ait
        kategoriler bu legacy uctan islenemez — herhangi biri brief'liyse
        HICBIR AI cagrisi yapilmadan ValueError yukselir (endpoint 409'a
        cevirir).
        """
        self._assert_categories_not_brief_scoped(request.category_ids)

        responses = []
        topic_terms: list = []
        excluded_block = "—"
        assignment_snapshot = None
        policy_loaded = False

        for category_id in request.category_ids:
            # Get category from DB
            db_category = self.db.query(SocialCategory).filter(
                SocialCategory.id == category_id
            ).first()

            if not db_category:
                logger.warning(f"Category {category_id} not found")
                continue

            # Stale parent invariant (plan B): stale kategori altında üretim YOK
            if db_category.is_stale:
                responses.append(SocialIdeasResponse(
                    category_id=category_id,
                    category_name=db_category.category_name,
                    total_ideas=0,
                    ideas=[],
                    policy_warnings=[PolicyWarning(
                        entity_type="category",
                        entity_id=category_id,
                        display_name=db_category.category_name,
                        reason_code="stale_parent",
                    )],
                ))
                continue

            if not policy_loaded:
                topic_terms, excluded_block = self._topic_policy_context(
                    db_category.scoring_run_id
                )
                assignment_snapshot = self._assignment_version(
                    db_category.scoring_run_id
                )
                policy_loaded = True

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
                target_platforms=target_platforms,
                excluded_topics=excluded_block,
            )

            # Deterministik topic engeli: eşleşen fikir KAYDEDİLMEZ
            idea_warnings: List[PolicyWarning] = []
            allowed_ideas = []
            for idea in idea_schemas:
                matched = self._match_topic(
                    f"{idea.idea_title} {idea.idea_description}", topic_terms
                )
                if matched:
                    logger.warning(
                        f"Fikir topic-policy reddi: '{idea.idea_title}' "
                        f"(matched_term={matched})"
                    )
                    idea_warnings.append(PolicyWarning(
                        entity_type="idea",
                        entity_id=None,
                        client_ref=idea.idea_title,
                        display_name=idea.idea_title,
                        reason_code="excluded_topic",
                        matched_term=matched,
                    ))
                    continue
                allowed_ideas.append(idea)

            # Kayit oncesi reassignment dogrulamasi (Codex A+B-1)
            if self._assignment_changed(db_category.scoring_run_id, assignment_snapshot):
                responses.append(SocialIdeasResponse(
                    category_id=category_id,
                    category_name=db_category.category_name,
                    total_ideas=0,
                    ideas=[],
                    policy_warnings=[*idea_warnings, PolicyWarning(
                        entity_type="category",
                        entity_id=category_id,
                        display_name=db_category.category_name,
                        reason_code="assignment_changed",
                    )],
                ))
                continue

            # Save to database
            saved_ideas = self._save_ideas(allowed_ideas, category_id)

            responses.append(SocialIdeasResponse(
                category_id=category_id,
                category_name=db_category.category_name,
                total_ideas=len(saved_ideas),
                ideas=saved_ideas,
                policy_warnings=idea_warnings,
            ))

        return responses
    
    # ==================== PHASE 3: CONTENTS ====================

    def generate_contents(
        self,
        request: SocialContentsRequest,
        scoring_run_id: Optional[int] = None,
    ) -> SocialContentsResponse:
        """
        Phase 3: Generate contents for selected ideas.

        scoring_run_id AÇIKÇA verilir (Faz F — ilk idea'dan tahmin edilmez);
        grounding whitelist'i (confirmed product_facts) bu run'dan yüklenir.
        Verilmezse ilk idea'nın kategorisinden türetilir (geriye uyum).

        Grounding (Faz F3): caption + hook'lar taranır; ihlalde 1 kez
        regenerate; ikinci kontrolde hâlâ ihlal varsa içerik DB'YE
        KAYDEDİLMEZ ve warnings listesinde raporlanır.

        Brief sınırları (plan_social_brief_akisi.md §7/§8): brief'e ait
        fikirler bu legacy yoldan üretilemez. Bu kontrol hem senkron
        endpoint'ten hem de Celery `social_contents_task`'ının çağırdığı
        yoldan geçer — brief fikri için legacy (brief_id IS NULL) bir
        SocialContent satırı YAZILMAZ (aksi halde `uq_social_content_
        idea_brief` kısmi tekil index'i kaçırılırdı).
        """
        self._assert_ideas_not_brief_scoped(request.idea_ids)

        contents = []
        warnings: List[SocialContentWarning] = []
        policy_warnings: List[PolicyWarning] = []

        if scoring_run_id is None:
            scoring_run_id = self._run_id_from_ideas(request.idea_ids)
        product_facts, grounding_facts = self._grounding_context(scoring_run_id)
        topic_terms, _ = self._topic_policy_context(scoring_run_id)
        assignment_snapshot = self._assignment_version(scoring_run_id)

        for idea_id in request.idea_ids:
            # Get idea from DB
            db_idea = self.db.query(SocialIdea).filter(SocialIdea.id == idea_id).first()

            if not db_idea:
                logger.warning(f"Idea {idea_id} not found")
                continue

            # Stale parent invariant (plan B): stale fikir/kategori altında üretim YOK
            parent_category = self.db.query(SocialCategory).filter(
                SocialCategory.id == db_idea.category_id
            ).first()
            if db_idea.is_stale or (parent_category and parent_category.is_stale):
                policy_warnings.append(PolicyWarning(
                    entity_type="idea",
                    entity_id=idea_id,
                    display_name=db_idea.idea_title,
                    reason_code="stale_parent",
                ))
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
                brand_tone=request.brand_tone,
                product_facts=product_facts,
            )

            if not content_schema:
                continue

            # Grounding kontrolü + tek regenerate şansı (Faz F3)
            content_schema, claim_warning = self._ground_or_reject(
                content_schema, idea, request.brand_name or "Marka",
                product_facts, grounding_facts,
            )
            if claim_warning is not None:
                warnings.append(claim_warning)
                continue  # KAYDEDİLMEZ — sessiz yayın yerine kayıt reddi

            # Topic policy (plan B): içerik yüzeyleri taranır; eşleşen KAYDEDİLMEZ
            topic_match = self._content_topic_match(content_schema, topic_terms)
            if topic_match:
                logger.warning(
                    f"İçerik topic-policy reddi (idea={idea_id}): "
                    f"matched_term={topic_match}"
                )
                policy_warnings.append(PolicyWarning(
                    entity_type="content",
                    entity_id=None,
                    client_ref=str(idea_id),
                    display_name=db_idea.idea_title,
                    reason_code="excluded_topic",
                    matched_term=topic_match,
                ))
                continue

            # Kayit oncesi reassignment dogrulamasi (Codex A+B-1)
            if self._assignment_changed(scoring_run_id, assignment_snapshot):
                policy_warnings.append(PolicyWarning(
                    entity_type="content",
                    entity_id=None,
                    client_ref=str(idea_id),
                    display_name=db_idea.idea_title,
                    reason_code="assignment_changed",
                ))
                continue

            saved_content = self._save_content(content_schema, idea_id)
            if saved_content:
                contents.append(saved_content)

        return SocialContentsResponse(
            idea_ids=request.idea_ids,
            total_contents=len(contents),
            contents=contents,
            warnings=warnings,
            policy_warnings=policy_warnings,
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
            return self._empty_response(
                request.scoring_run_id,
                policy_warnings=cat_response.policy_warnings,
            )
        
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
        
        # Phase 3: Contents for selected ideas (run kimliği AÇIK — Faz F)
        content_request = SocialContentsRequest(
            idea_ids=[i.id for i in selected_ideas],
            brand_name=request.brand_name
        )
        content_response = self.generate_contents(
            content_request, scoring_run_id=request.scoring_run_id
        )

        bulk_policy_warnings = list(cat_response.policy_warnings)
        for resp in idea_responses:
            bulk_policy_warnings.extend(resp.policy_warnings)
        bulk_policy_warnings.extend(content_response.policy_warnings)

        return SocialBulkResponse(
            scoring_run_id=request.scoring_run_id,
            total_categories=len(cat_response.categories),
            total_ideas=len(all_ideas),
            total_contents=len(content_response.contents),
            categories=cat_response.categories,
            selected_ideas=selected_ideas,
            contents=content_response.contents,
            warnings=content_response.warnings,
            policy_warnings=bulk_policy_warnings,
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
        self._raise_if_brief_idea(db_idea)
        self._raise_if_stale_idea(db_idea)
        self._raise_if_idea_has_content(db_idea)

        db_category = self.db.query(SocialCategory).filter(
            SocialCategory.id == db_idea.category_id
        ).first()
        
        if not db_category:
            return None

        topic_terms, excluded_block = self._topic_policy_context(
            db_category.scoring_run_id
        )
        
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
            additional_context=additional_context,
            excluded_topics=excluded_block,
        )

        # Topic policy: yeni fikir dislanan konudaysa MEVCUT fikir korunur
        if new_idea:
            matched = self._match_topic(
                f"{new_idea.idea_title} {new_idea.idea_description}", topic_terms
            )
            if matched:
                raise ValueError(f"EXCLUDED_TOPIC: {matched}")
        
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
        self._raise_if_brief_content(db_content)
        if db_content.is_stale:
            raise ValueError("STALE: icerik bayat - kanal atamasi yenilendi")

        db_idea = self.db.query(SocialIdea).filter(SocialIdea.id == db_content.idea_id).first()
        if not db_idea:
            return None
        self._raise_if_stale_idea(db_idea)
        
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
        
        # Grounding bağlamı (Faz F): run kimliği kategori üzerinden
        run_id = self._run_id_from_ideas([db_idea.id])
        product_facts, grounding_facts = self._grounding_context(run_id)

        # Regenerate
        new_content = self.content_gen.regenerate(
            old_content=old_content,
            idea=idea,
            brand_name=brand_name,
            additional_context=additional_context,
            product_facts=product_facts,
        )

        # Topic policy (plan B): yeni icerik dislanan konudaysa mevcut korunur
        if new_content is not None:
            topic_terms, _ = self._topic_policy_context(run_id)
            topic_match = self._content_topic_match(new_content, topic_terms)
            if topic_match:
                raise ValueError(f"EXCLUDED_TOPIC: {topic_match}")

        # Grounding reddi (Faz F): yeni içerik desteksiz iddia taşıyorsa
        # MEVCUT TEMİZ İÇERİK OVERWRITE EDİLMEZ
        if new_content is not None:
            claims = self._find_content_claims(new_content, grounding_facts)
            if claims:
                logger.warning(
                    f"Regenerated social content rejected (content={content_id}): "
                    f"desteksiz iddia — mevcut içerik korunuyor. claims={claims}"
                )
                return None

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
    
    def get_all(self, scoring_run_id: int, include_stale: bool = False) -> SocialFullResponse:
        """Get all social media data for a scoring run.

        Parent-child invariant (plan B): varsayilan gorunum category + idea +
        content UCUNUN de non-stale olmasini dogrular; include_stale=true
        yalniz audit/gecmis incelemesi icindir.

        Brief sınırı (plan_social_brief_akisi.md §7/§8): bu legacy okuma
        yalnız `brief_id IS NULL` satırları döner — brief akışının kendi
        salt-okunur ucları (`/generation/social/briefs/{id}` vb.) brief'e
        ait kategori/fikir/içeriği ayrıca sunar.
        """
        cat_query = self.db.query(SocialCategory).filter(
            SocialCategory.scoring_run_id == scoring_run_id,
            SocialCategory.brief_id.is_(None),
        )
        if not include_stale:
            cat_query = cat_query.filter(SocialCategory.is_stale.is_(False))
        categories = cat_query.all()

        cat_schemas = [self._category_to_schema(c) for c in categories]

        category_ids = [c.id for c in categories]
        ideas = []
        if category_ids:
            idea_query = self.db.query(SocialIdea).filter(
                SocialIdea.category_id.in_(category_ids),
                SocialIdea.brief_id.is_(None),
            )
            if not include_stale:
                idea_query = idea_query.filter(SocialIdea.is_stale.is_(False))
            ideas = idea_query.all()

        idea_schemas = [self._idea_to_schema(i) for i in ideas]

        idea_ids = [i.id for i in ideas]
        contents = []
        if idea_ids:
            content_query = self.db.query(SocialContent).filter(
                SocialContent.idea_id.in_(idea_ids),
                SocialContent.brief_id.is_(None),
            )
            if not include_stale:
                content_query = content_query.filter(SocialContent.is_stale.is_(False))
            contents = content_query.all()

        content_schemas = [self._content_to_schema(c) for c in contents]
        
        return SocialFullResponse(
            scoring_run_id=scoring_run_id,
            categories=cat_schemas,
            ideas=idea_schemas,
            contents=content_schemas
        )
    
    def select_idea(self, idea_id: int, selected: bool = True) -> bool:
        """Select or deselect an idea. Stale fikir/kategori secilemez (plan B)."""
        idea = self.db.query(SocialIdea).filter(SocialIdea.id == idea_id).first()
        if idea is None:
            return False
        self._raise_if_brief_idea(idea)
        self._raise_if_stale_idea(idea)
        idea.is_selected = selected
        self.db.commit()
        return True

    def _raise_if_stale_idea(self, db_idea) -> None:
        """Stale entity uzerinden islem 409'a cevrilir (endpoint mapping)."""
        if db_idea.is_stale:
            raise ValueError("STALE: fikir bayat - kanal atamasi yenilendi")
        category = self.db.query(SocialCategory).filter(
            SocialCategory.id == db_idea.category_id
        ).first()
        if category is not None and category.is_stale:
            raise ValueError("STALE: kategori bayat - kanal atamasi yenilendi")

    # ==================== BRIEF SINIRI (plan_social_brief_akisi.md §7/§8) ====================

    def _raise_if_brief_idea(self, db_idea) -> None:
        """Brief'e ait fikir legacy uctan islenemez (409'a cevrilir)."""
        if db_idea.brief_id is not None:
            raise ValueError(
                "BRIEF_SCOPED: fikir bir brief'e ait; legacy uctan islenemez, "
                "/generation/social/briefs kullanin"
            )

    def _raise_if_brief_content(self, db_content) -> None:
        """Brief'e ait icerik legacy uctan islenemez (409'a cevrilir)."""
        if db_content.brief_id is not None:
            raise ValueError(
                "BRIEF_SCOPED: icerik bir brief'e ait; legacy uctan islenemez, "
                "/generation/social/briefs kullanin"
            )

    def _raise_if_idea_has_content(self, db_idea) -> None:
        """K10: icerigi zaten uretilmis bir fikir yeniden uretilemez —
        fikir DEGISTIRILMEZ, kullanici mevcut icerigi yeniden uretmeye
        veya yeni bir fikir olusturmaya yonlendirilir."""
        existing = (
            self.db.query(SocialContent.id)
            .filter(SocialContent.idea_id == db_idea.id)
            .first()
        )
        if existing:
            raise ValueError(
                "Bu fikrin içeriği var. Mevcut içeriği yeniden üretin veya "
                "yeni bir fikir oluşturun."
            )

    def _assert_categories_not_brief_scoped(self, category_ids: List[int]) -> None:
        """Legacy /social/ideas: brief'e ait kategori varsa HICBIR AI
        cagrisi yapilmadan reddedilir."""
        if not category_ids:
            return
        rows = (
            self.db.query(SocialCategory.id)
            .filter(
                SocialCategory.id.in_(category_ids),
                SocialCategory.brief_id.isnot(None),
            )
            .all()
        )
        if rows:
            ids = ", ".join(str(r[0]) for r in rows)
            raise ValueError(
                f"BRIEF_SCOPED: kategori(ler) {ids} bir brief'e ait; legacy "
                "uctan islenemez, /generation/social/briefs kullanin"
            )

    def _assert_ideas_not_brief_scoped(self, idea_ids: List[int]) -> None:
        """Legacy /social/contents (+ async + Celery task): brief'e ait
        fikir varsa HICBIR AI cagrisi yapilmadan reddedilir — brief fikri
        icin legacy icerik satiri asla yazilmaz."""
        if not idea_ids:
            return
        rows = (
            self.db.query(SocialIdea.id)
            .filter(SocialIdea.id.in_(idea_ids), SocialIdea.brief_id.isnot(None))
            .all()
        )
        if rows:
            ids = ", ".join(str(r[0]) for r in rows)
            raise ValueError(
                f"BRIEF_SCOPED: fikir(ler) {ids} bir brief'e ait; legacy "
                "uctan islenemez, /generation/social/briefs kullanin"
            )

    # ============ REASSIGNMENT YARISI KORUMASI (Codex A+B-1) ============

    def _assignment_version(self, scoring_run_id, lock: bool = False):
        """Run'in reassignment sayacini okur (lock=True: FOR UPDATE)."""
        from app.database.models import ScoringRun

        if not scoring_run_id:
            return None
        query = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id)
        if lock:
            query = query.with_for_update()
        run = query.first()
        if run is None:
            return None
        return run.channel_assignment_version or 1

    def _assignment_changed(self, scoring_run_id, snapshot) -> bool:
        """Kayittan ONCE cagrilir: uretim suresince reassignment oldu mu?

        Run satiri FOR UPDATE ile kilitlenir — kayit commit'ine kadar yeni
        bir reassignment versiyonu ARTIRAMAZ (transition ayni satiri
        guncellemek icin bekler). Boylece 'AI sirasinda reassignment →
        eski havuzdan fresh cikti' yarisi kapanir.
        """
        if not scoring_run_id or snapshot is None:
            return False
        current = self._assignment_version(scoring_run_id, lock=True)
        return current is not None and current != snapshot

    # ==================== TOPIC POLICY (plan B) ====================

    def _topic_policy_context(self, scoring_run_id: Optional[int]):
        """(approved_terms, prompt_block) — deterministik engel YALNIZ
        approved term/alias'larla; exclude_themes yalnız prompt yönlendirmesi."""
        from app.core.policy.topic_policy import approved_topic_terms
        from app.database.models import BrandProfile, ScoringRun

        if not scoring_run_id:
            return [], "—"
        run = (
            self.db.query(ScoringRun)
            .filter(ScoringRun.id == scoring_run_id)
            .first()
        )
        if run is None or not run.brand_profile_id:
            return [], "—"
        profile = (
            self.db.query(BrandProfile)
            .filter(BrandProfile.id == run.brand_profile_id)
            .first()
        )
        if profile is None:
            return [], "—"
        themes = (profile.profile_data or {}).get("exclude_themes") or []
        terms = approved_topic_terms(profile)
        display = list(dict.fromkeys([*themes, *terms]))
        block = "\n".join(f"- {t}" for t in display) or "—"
        return terms, block

    @staticmethod
    def _match_topic(text: str, terms) -> Optional[str]:
        from app.core.policy.topic_policy import match_topic_term
        return match_topic_term(text or "", terms)

    def _content_topic_match(self, content: SocialContentSchema, terms) -> Optional[str]:
        """İçerik yüzeylerinin tamamı taranır (Codex v5: yalnız başlık değil)."""
        surfaces = [
            content.caption or "",
            content.scenario or "",
            content.cta_text or "",
            # Codex A+B-5: SAKLANAN tum metin yuzeyleri taranir
            content.visual_suggestion or "",
            content.video_concept or "",
            content.industry_posting_suggestion or "",
            content.platform_notes or "",
            " ".join(content.hashtags or []),
        ]
        surfaces.extend(h.text for h in (content.hooks or []))
        for surface in surfaces:
            matched = self._match_topic(surface, terms)
            if matched:
                return matched
        return None

    # ==================== GROUNDING (Faz F) ====================

    def _run_id_from_ideas(self, idea_ids: List[int]) -> Optional[int]:
        """İlk bulunan idea'nın kategorisinden run kimliği (geriye uyum)."""
        for idea_id in idea_ids or []:
            row = (
                self.db.query(SocialCategory.scoring_run_id)
                .join(SocialIdea, SocialIdea.category_id == SocialCategory.id)
                .filter(SocialIdea.id == idea_id)
                .first()
            )
            if row:
                return row[0]
        return None

    def _grounding_context(self, scoring_run_id: Optional[int]) -> tuple:
        """(product_facts, grounding_facts) — confirmed profil ürün bilgisi.

        SOCIAL isteklerinde kullanıcı USP alanı yok; whitelist yalnız
        confirmed product_facts'ten kurulur. Boşsa hiçbir sayısal iddia geçemez.
        """
        from app.core.channel.brand_defense import load_product_definition
        from app.database.models import ScoringRun

        if not scoring_run_id:
            return "", ""
        run = (
            self.db.query(ScoringRun)
            .filter(ScoringRun.id == scoring_run_id)
            .first()
        )
        product_facts = load_product_definition(self.db, run) or ""
        return product_facts, product_facts

    def _find_content_claims(
        self, content: SocialContentSchema, grounding_facts: str
    ) -> List[str]:
        """Caption + hook metinlerindeki desteksiz iddiaları toplar."""
        claims: List[str] = []
        for hook in content.hooks or []:
            claims.extend(find_ungrounded_claims(hook.text, grounding_facts))
        claims.extend(find_ungrounded_claims(content.caption or "", grounding_facts))
        # Tekilleştir, sırayı koru
        seen = set()
        return [c for c in claims if not (c in seen or seen.add(c))]

    def _ground_or_reject(
        self,
        content: SocialContentSchema,
        idea: SocialIdeaSchema,
        brand_name: str,
        product_facts: str,
        grounding_facts: str,
    ):
        """(content, None) temizse; ihlalde 1 regenerate; hâlâ ihlalliyse
        (None, SocialContentWarning) — çağıran içeriği KAYDETMEZ."""
        claims = self._find_content_claims(content, grounding_facts)
        if not claims:
            return content, None

        logger.warning(
            f"Social content ungrounded claims (idea={idea.id}): {claims} — "
            f"1 kez yeniden üretiliyor"
        )
        regenerated = self.content_gen.regenerate(
            old_content=content,
            idea=idea,
            brand_name=brand_name,
            additional_context=(
                "Önceki içerik desteksiz iddia içeriyordu, bunları KULLANMA: "
                + "; ".join(claims)
            ),
            product_facts=product_facts,
        )
        if regenerated is not None:
            second_claims = self._find_content_claims(regenerated, grounding_facts)
            if not second_claims:
                return regenerated, None
            claims = second_claims

        logger.warning(
            f"Social content REJECTED (idea={idea.id}): desteksiz iddia "
            f"çözülemedi, kaydedilmiyor — claims={claims}"
        )
        return None, SocialContentWarning(
            idea_id=idea.id or 0,
            reason_code="ungrounded_claim",
            claims=claims,
        )

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
    
    def _empty_response(
        self, scoring_run_id: int, policy_warnings=None
    ) -> SocialBulkResponse:
        """Bos response — policy warning'leri KAYBETMEZ (Codex A+B-2):
        tum kategoriler reddedildiginde 'tumu ret = failed + warnings'
        sozlesmesi task katmaninda ancak bu tasima ile calisir."""
        return SocialBulkResponse(
            scoring_run_id=scoring_run_id,
            total_categories=0,
            total_ideas=0,
            total_contents=0,
            categories=[],
            selected_ideas=[],
            contents=[],
            policy_warnings=list(policy_warnings or []),
            generated_at=datetime.utcnow()
        )
