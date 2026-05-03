"""
Google Ads Generator - Main Orchestrator.

Orchestrates the complete RSA generation pipeline:
1. Get ADS pool keywords from scoring run
2. Group keywords by intent (KeywordGrouper)
3. Generate RSA for each group (RSAGenerator)
4. Save to database (atomic transaction)
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from loguru import logger

from app.config import settings
from app.generators.ai_service import AIService
from app.generators.ads.keyword_grouper import KeywordGrouper
from app.generators.ads.rsa_generator import RSAGenerator
from app.database.models import (
    AdGroup, AdHeadline, AdDescription, NegativeKeyword,
    ChannelPool, Keyword, ScoringRun
)
from app.schemas.ads import (
    AdsGenerateRequest,
    AdsGenerateResponse,
    AdGroupFullSchema,
    AdGroupDBSchema,
    AdGroupListResponse,
    KeywordForGrouping,
    ValidationSummary,
    HeadlineDBSchema,
    DescriptionDBSchema,
    NegativeKeywordDBSchema,
)


class AdsGenerator:
    """
    Google Ads RSA generation orchestrator.
    
    Usage:
        generator = AdsGenerator(db, ai_service)
        result = generator.generate_ads(request)
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service
        self.grouper = KeywordGrouper(ai_service)
        self.rsa_generator = None  # Created per request for enable_ai_regeneration flag
    
    def generate_ads(self, request: AdsGenerateRequest) -> AdsGenerateResponse:
        """
        Generate complete RSA set for ADS pool keywords.
        
        Steps:
        1. Get ADS pool keywords from scoring run
        2. Group keywords by intent
        3. Generate RSA for each group
        4. Validate all components
        5. Save to database (atomic)
        6. Return response
        """
        logger.info(f"Starting ads generation for scoring_run_id={request.scoring_run_id}")
        
        # Initialize RSA generator with request settings
        self.rsa_generator = RSAGenerator(
            ai_service=self.ai_service,
            enable_ai_regeneration=request.enable_ai_regeneration,
            enable_fallback=settings.ADS_FALLBACK_ENABLED
        )
        
        # Step 1: Get ADS pool keywords
        keywords = self._get_ads_keywords(request.scoring_run_id)
        
        if not keywords:
            logger.warning(f"No ADS keywords found for scoring_run_id={request.scoring_run_id}")
            return self._empty_response(request.scoring_run_id)
        
        logger.info(f"Found {len(keywords)} keywords in ADS pool")
        
        # Apply max_groups limit if specified
        if request.max_groups:
            max_keywords = request.max_groups * 5  # Approximate
            keywords = keywords[:max_keywords]
        
        # Step 2: Group keywords
        keyword_inputs = [
            KeywordForGrouping(id=k["id"], keyword=k["keyword"])
            for k in keywords
        ]
        groups = self.grouper.group_keywords(keyword_inputs)
        
        logger.info(f"Created {len(groups)} keyword groups")
        
        # Step 3 & 4: Generate and validate RSA for each group
        ad_groups: List[AdGroupFullSchema] = []
        total_validation_stats = self._init_validation_stats()
        
        for group in groups:
            try:
                ad_group = self.rsa_generator.generate_rsa(
                    group=group,
                    brand_name=request.brand_name or "",
                    brand_usp=request.brand_usp or ""
                )
                ad_groups.append(ad_group)
                
                # Aggregate validation stats
                self._aggregate_stats(total_validation_stats, ad_group)
                
            except Exception as e:
                logger.error(f"Failed to generate RSA for group '{group.name}': {e}")
                continue
        
        # Step 5: Save to database (atomic transaction)
        saved_groups = self._save_to_database(ad_groups, request.scoring_run_id)
        
        # Step 6: Build response
        return AdsGenerateResponse(
            scoring_run_id=request.scoring_run_id,
            total_keywords=len(keywords),
            total_groups=len(saved_groups),
            ad_groups=ad_groups,
            total_headlines=sum(len(g.headlines) for g in ad_groups),
            total_descriptions=sum(len(g.descriptions) for g in ad_groups),
            total_negative_keywords=sum(len(g.negative_keywords) for g in ad_groups),
            validation_summary=ValidationSummary(**total_validation_stats),
            generated_at=datetime.utcnow()
        )
    
    def get_ad_groups(self, scoring_run_id: int) -> AdGroupListResponse:
        """Get all ad groups for a scoring run."""
        groups = self.db.query(AdGroup).filter(
            AdGroup.scoring_run_id == scoring_run_id
        ).all()
        
        return AdGroupListResponse(
            scoring_run_id=scoring_run_id,
            total_groups=len(groups),
            ad_groups=[self._model_to_schema(g) for g in groups]
        )
    
    def get_ad_group_detail(self, group_id: int) -> Optional[AdGroupDBSchema]:
        """Get detailed view of a single ad group."""
        group = self.db.query(AdGroup).filter(AdGroup.id == group_id).first()
        
        if not group:
            return None
        
        return self._model_to_schema(group)
    
    def regenerate_group(
        self, 
        group_id: int,
        brand_name: str = "",
        brand_usp: str = ""
    ) -> Optional[AdGroupFullSchema]:
        """Regenerate RSA for a specific group."""
        group = self.db.query(AdGroup).filter(AdGroup.id == group_id).first()
        
        if not group:
            return None
        
        # Delete existing components
        self.db.query(AdHeadline).filter(AdHeadline.ad_group_id == group_id).delete()
        self.db.query(AdDescription).filter(AdDescription.ad_group_id == group_id).delete()
        self.db.query(NegativeKeyword).filter(NegativeKeyword.ad_group_id == group_id).delete()
        
        # Regenerate
        from app.schemas.ads import KeywordGroupSchema
        input_group = KeywordGroupSchema(
            name=group.group_name,
            theme=group.group_theme or "",
            keyword_ids=group.target_keyword_ids or [],
            keywords=group.target_keywords or []
        )
        
        self.rsa_generator = RSAGenerator(
            ai_service=self.ai_service,
            enable_ai_regeneration=True,
            enable_fallback=settings.ADS_FALLBACK_ENABLED
        )
        
        new_group = self.rsa_generator.generate_rsa(
            group=input_group,
            brand_name=brand_name,
            brand_usp=brand_usp
        )
        
        # Update database
        self._update_group_in_db(group, new_group)
        self.db.commit()
        
        return new_group
    
    # ==================== PRIVATE METHODS ====================
    
    def _get_ads_keywords(self, scoring_run_id: int) -> List[dict]:
        """Get keywords from ADS pool (pre-filter enriched)."""
        pools = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == "ADS"
        ).all()
        
        keywords = []
        for pool in pools:
            kw = self.db.query(Keyword).filter(Keyword.id == pool.keyword_id).first()
            if kw:
                keywords.append({
                    "id": kw.id,
                    "keyword": kw.keyword,
                    "sector": kw.sector
                })
        
        # Pre-filter enrichment
        try:
            from app.core.channel.pre_filters.enricher import PreFilterEnricher
            enricher = PreFilterEnricher(self.db)
            keywords = enricher.enrich_keywords(scoring_run_id, "ADS", keywords)
        except Exception:
            pass  # Fail-open: enrichment başarısız olursa devam et
        
        return keywords
    
    def _save_to_database(
        self, 
        ad_groups: List[AdGroupFullSchema],
        scoring_run_id: int
    ) -> List[AdGroup]:
        """
        Save all ad groups to database in atomic transaction.
        
        If any save fails, all changes are rolled back.
        """
        saved = []
        
        try:
            for group in ad_groups:
                # Create AdGroup
                db_group = AdGroup(
                    scoring_run_id=scoring_run_id,
                    group_name=group.name,
                    group_theme=group.theme,
                    target_keyword_ids=group.keyword_ids,
                    target_keywords=group.keywords,
                    headlines_generated=group.headlines_generated,
                    headlines_eliminated=group.headlines_eliminated,
                    dki_converted_count=group.dki_converted_count,
                )
                self.db.add(db_group)
                self.db.flush()  # Get ID
                
                # Create Headlines
                for i, h in enumerate(group.headlines):
                    db_headline = AdHeadline(
                        ad_group_id=db_group.id,
                        headline_text=h.text,
                        headline_type=h.type,
                        position_preference=h.position_preference,
                        is_dki=h.is_dki,
                        sort_order=i,
                    )
                    self.db.add(db_headline)
                
                # Create Descriptions
                for i, d in enumerate(group.descriptions):
                    db_desc = AdDescription(
                        ad_group_id=db_group.id,
                        description_text=d.text,
                        description_type=d.type,
                        position_preference=d.position_preference,
                        sort_order=i,
                    )
                    self.db.add(db_desc)
                
                # Create Negative Keywords
                for n in group.negative_keywords:
                    db_neg = NegativeKeyword(
                        ad_group_id=db_group.id,
                        keyword=n.keyword,
                        match_type=n.match_type,
                        category=n.category,
                        reason=n.reason,
                    )
                    self.db.add(db_neg)
                
                saved.append(db_group)
            
            # Commit all at once (atomic)
            self.db.commit()
            logger.info(f"Saved {len(saved)} ad groups to database")
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Database save failed, rolling back: {e}")
            raise
        
        return saved
    
    def _update_group_in_db(self, db_group: AdGroup, new_data: AdGroupFullSchema):
        """Update existing group with new data."""
        db_group.headlines_generated = new_data.headlines_generated
        db_group.headlines_eliminated = new_data.headlines_eliminated
        db_group.dki_converted_count = new_data.dki_converted_count
        
        for i, h in enumerate(new_data.headlines):
            db_headline = AdHeadline(
                ad_group_id=db_group.id,
                headline_text=h.text,
                headline_type=h.type,
                position_preference=h.position_preference,
                is_dki=h.is_dki,
                sort_order=i,
            )
            self.db.add(db_headline)
        
        for i, d in enumerate(new_data.descriptions):
            db_desc = AdDescription(
                ad_group_id=db_group.id,
                description_text=d.text,
                description_type=d.type,
                position_preference=d.position_preference,
                sort_order=i,
            )
            self.db.add(db_desc)
        
        for n in new_data.negative_keywords:
            db_neg = NegativeKeyword(
                ad_group_id=db_group.id,
                keyword=n.keyword,
                match_type=n.match_type,
                category=n.category,
                reason=n.reason,
            )
            self.db.add(db_neg)
    
    def _model_to_schema(self, group: AdGroup) -> AdGroupDBSchema:
        """Convert database model to schema."""
        return AdGroupDBSchema(
            id=group.id,
            group_name=group.group_name,
            group_theme=group.group_theme,
            target_keyword_ids=group.target_keyword_ids or [],
            target_keywords=group.target_keywords or [],
            headlines=[HeadlineDBSchema(
                id=h.id,
                text=h.headline_text,
                type=h.headline_type or "keyword",
                position_preference=h.position_preference or "any",
                is_dki=h.is_dki or False,
                validation_action=h.validation_action,
                original_length=h.original_length,
                sort_order=h.sort_order or 0,
            ) for h in group.headlines],
            descriptions=[DescriptionDBSchema(
                id=d.id,
                text=d.description_text,
                type=d.description_type or "value_prop",
                position_preference=d.position_preference or "any",
                validation_action=d.validation_action,
                sort_order=d.sort_order or 0,
            ) for d in group.descriptions],
            negative_keywords=[NegativeKeywordDBSchema(
                id=n.id,
                keyword=n.keyword,
                match_type=n.match_type or "phrase",
                category=n.category,
                reason=n.reason,
            ) for n in group.negative_keywords],
            headlines_generated=group.headlines_generated or 0,
            headlines_eliminated=group.headlines_eliminated or 0,
            dki_converted_count=group.dki_converted_count or 0,
            created_at=group.created_at,
        )
    
    def _init_validation_stats(self) -> dict:
        """Initialize validation stats dictionary."""
        return {
            "total_headlines_generated": 0,
            "headlines_kept": 0,
            "headlines_shortened": 0,
            "headlines_regenerated": 0,
            "headlines_eliminated": 0,
            "dki_converted_to_plain": 0,
            "total_descriptions_generated": 0,
            "descriptions_kept": 0,
            "descriptions_sentence_trimmed": 0,
            "descriptions_truncated": 0,
        }
    
    def _aggregate_stats(self, total: dict, group: AdGroupFullSchema):
        """Aggregate stats from a group into total."""
        total["total_headlines_generated"] += group.headlines_generated
        total["headlines_eliminated"] += group.headlines_eliminated
        total["headlines_shortened"] += group.headlines_shortened
        total["headlines_regenerated"] += group.headlines_regenerated
        total["headlines_kept"] += (
            group.headlines_generated 
            - group.headlines_eliminated 
            - group.headlines_shortened 
            - group.headlines_regenerated
        )
        total["dki_converted_to_plain"] += group.dki_converted_count
        total["total_descriptions_generated"] += len(group.descriptions)
        total["descriptions_kept"] += len(group.descriptions)  # Simplified
    
    def _empty_response(self, scoring_run_id: int) -> AdsGenerateResponse:
        """Return empty response when no keywords found."""
        return AdsGenerateResponse(
            scoring_run_id=scoring_run_id,
            total_keywords=0,
            total_groups=0,
            ad_groups=[],
            total_headlines=0,
            total_descriptions=0,
            total_negative_keywords=0,
            validation_summary=ValidationSummary(
                total_headlines_generated=0,
                headlines_kept=0,
                headlines_shortened=0,
                headlines_regenerated=0,
                headlines_eliminated=0,
                dki_converted_to_plain=0,
                total_descriptions_generated=0,
                descriptions_kept=0,
                descriptions_sentence_trimmed=0,
                descriptions_truncated=0,
            ),
            generated_at=datetime.utcnow()
        )
