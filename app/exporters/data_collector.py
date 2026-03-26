"""
Export Data Collector.

Veritabanından export için veri toplayan merkezi sınıf.
"""
from datetime import datetime
from typing import List, Optional, Any, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.schemas.export import (
    ExportSectionEnum, FullReport, SummaryData, ScoringData,
    KeywordScoreData, ChannelsData, ChannelPoolData,
    SEOContentsData, SEOContentData, AdsData, AdGroupData,
    HeadlineData, DescriptionData, NegativeKeywordData,
    SocialData, SocialContentData, HookData, BrandProfileData,
    BrandProfileCompetitorData
)
from app.database.models import (
    Keyword, ScoringRun, ChannelPool, KeywordScore, IntentAnalysis,
    ContentOutput,
    SEOGeoContent, SEOComplianceResult, GEOComplianceResult,
    AdGroup, AdHeadline, AdDescription, NegativeKeyword,
    SocialCategory, SocialIdea, SocialContent, BrandProfile, KeywordRelevance
)


class ExportDataCollector:
    """
    Veritabanından export için veri toplar.
    
    Her bölüm için ayrı collection metodu vardır.
    Sadece istenen bölümler toplanır.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def collect(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum]
    ) -> FullReport:
        """
        Belirtilen bölümler için verileri toplar.
        """
        include_all = ExportSectionEnum.ALL in sections
        
        # Summary her zaman dahil
        summary = self._collect_summary(scoring_run_id)
        
        scoring = None
        if include_all or ExportSectionEnum.SCORING in sections:
            scoring = self._collect_scoring(scoring_run_id)

        brand_profile = None
        if include_all or ExportSectionEnum.BRAND_PROFILE in sections:
            brand_profile = self._collect_brand_profile(scoring_run_id)
        
        channels = None
        if include_all or ExportSectionEnum.CHANNELS in sections:
            channels = self._collect_channels(scoring_run_id)
        
        seo_contents = None
        if include_all or ExportSectionEnum.SEO_CONTENT in sections:
            seo_contents = self._collect_seo_contents(scoring_run_id)
        
        ads = None
        if include_all or ExportSectionEnum.ADS in sections:
            ads = self._collect_ads(scoring_run_id)
        
        social = None
        if include_all or ExportSectionEnum.SOCIAL in sections:
            social = self._collect_social(scoring_run_id)
        
        return FullReport(
            scoring_run_id=scoring_run_id,
            generated_at=datetime.utcnow(),
            summary=summary,
            brand_profile=brand_profile,
            scoring=scoring,
            channels=channels,
            seo_contents=seo_contents,
            ads=ads,
            social=social
        )

    def _collect_brand_profile(self, scoring_run_id: int) -> Optional[BrandProfileData]:
        """Run icin marka profilini toplar."""
        profile = self.db.query(BrandProfile).filter(
            BrandProfile.scoring_run_id == scoring_run_id
        ).first()

        if not profile:
            return None

        profile_data: Dict[str, Any] = profile.profile_data or {}
        validation_data: Dict[str, Any] = profile.validation_data or {}

        competitors: List[BrandProfileCompetitorData] = []
        for comp in validation_data.get("competitors", []) or []:
            if not isinstance(comp, dict):
                continue
            raw_score = comp.get("consistency_score")
            try:
                score = float(raw_score) if raw_score is not None else None
            except (TypeError, ValueError):
                score = None
            competitors.append(BrandProfileCompetitorData(
                url=comp.get("url") or "",
                status=comp.get("status") or comp.get("verdict"),
                summary=comp.get("summary") or comp.get("analysis"),
                consistency_score=score
            ))

        return BrandProfileData(
            status=profile.status or "pending",
            company_url=profile.company_url,
            competitor_urls=profile.competitor_urls or [],
            company_name=profile_data.get("company_name"),
            sector=profile_data.get("sector"),
            target_audience=profile_data.get("target_audience"),
            products=profile_data.get("products") or [],
            services=profile_data.get("services") or [],
            use_cases=profile_data.get("use_cases") or [],
            problems_solved=profile_data.get("problems_solved") or [],
            brand_terms=profile_data.get("brand_terms") or [],
            exclude_themes=profile_data.get("exclude_themes") or [],
            anchor_texts=profile_data.get("anchor_texts") or [],
            source_pages=profile.source_pages or [],
            competitors=competitors,
            validation_warnings=validation_data.get("warnings") or [],
            error_message=profile.error_message
        )
    
    def _collect_summary(self, scoring_run_id: int) -> SummaryData:
        """Özet istatistikleri toplar."""
        run = self.db.query(ScoringRun).filter(
            ScoringRun.id == scoring_run_id
        ).first()
        
        created_at = run.created_at if run else datetime.utcnow()
        
        # Keyword count - keywords link via KeywordScore
        total_keywords = self.db.query(KeywordScore).filter(
            KeywordScore.scoring_run_id == scoring_run_id
        ).count()
        
        # Channel counts
        ads_count = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'ADS'
        ).count()
        
        seo_count = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'SEO'
        ).count()
        
        social_count = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'SOCIAL'
        ).count()
        
        # Strategic count
        strategic_count = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.is_strategic == True
        ).count()
        
        # Content counts - SEO contents via ChannelPool
        seo_keyword_ids = [p.keyword_id for p in self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'SEO'
        ).all()]
        seo_content_count = self.db.query(SEOGeoContent).filter(
            SEOGeoContent.keyword_id.in_(seo_keyword_ids)
        ).count() if seo_keyword_ids else 0
        
        ad_group_count = self.db.query(AdGroup).filter(
            AdGroup.scoring_run_id == scoring_run_id
        ).count()
        
        social_content_count = self.db.query(SocialContent).join(
            SocialIdea, SocialContent.idea_id == SocialIdea.id
        ).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id
        ).count()
        
        return SummaryData(
            scoring_run_id=scoring_run_id,
            created_at=created_at,
            total_keywords=total_keywords,
            ads_count=ads_count,
            seo_count=seo_count,
            social_count=social_count,
            strategic_count=strategic_count,
            seo_content_count=seo_content_count,
            ad_group_count=ad_group_count,
            social_content_count=social_content_count
        )
    
    def _collect_scoring(self, scoring_run_id: int) -> ScoringData:
        """Tüm keyword skorlarını toplar."""
        scores = self.db.query(KeywordScore).filter(
            KeywordScore.scoring_run_id == scoring_run_id
        ).all()
        
        keyword_data = []
        for ks in scores:
            kw = self.db.query(Keyword).filter(Keyword.id == ks.keyword_id).first()
            if not kw:
                continue
            
            # Determine primary channel from ChannelPool
            pool = self.db.query(ChannelPool).filter(
                ChannelPool.scoring_run_id == scoring_run_id,
                ChannelPool.keyword_id == kw.id
            ).first()
            
            # Intent bilgisini çek (channel filtreli)
            intent_filters = [
                IntentAnalysis.scoring_run_id == scoring_run_id,
                IntentAnalysis.keyword_id == kw.id,
                IntentAnalysis.is_passed == True,
            ]
            if pool:
                intent_filters.append(IntentAnalysis.channel == pool.channel)
            intent_record = self.db.query(IntentAnalysis).filter(
                *intent_filters
            ).first()
            
            keyword_data.append(KeywordScoreData(
                keyword_id=kw.id,
                keyword=kw.keyword,
                volume=kw.monthly_volume,
                trend_3m=float(kw.trend_3m) if kw.trend_3m else None,
                trend_12m=float(kw.trend_12m) if kw.trend_12m else None,
                competition=float(kw.competition_score) if kw.competition_score else None,
                ads_score=float(ks.ads_score) if ks.ads_score else None,
                seo_score=float(ks.seo_score) if ks.seo_score else None,
                social_score=float(ks.social_score) if ks.social_score else None,
                ads_rank=ks.ads_rank,
                seo_rank=ks.seo_rank,
                social_rank=ks.social_rank,
                primary_channel=pool.channel if pool else None,
                intent=intent_record.intent_type if intent_record else None
            ))
        
        return ScoringData(keywords=keyword_data, total=len(keyword_data))
    
    def _collect_channels(self, scoring_run_id: int) -> ChannelsData:
        """Kanal havuzlarını toplar."""
        relevance_fallback_map = {
            keyword_id: float(relevance_score)
            for keyword_id, relevance_score in (
                self.db.query(KeywordRelevance.keyword_id, KeywordRelevance.relevance_score)
                .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
                .all()
            )
            if relevance_score is not None
        }
        
        def get_pool(channel: str) -> ChannelPoolData:
            pools = self.db.query(ChannelPool).filter(
                ChannelPool.scoring_run_id == scoring_run_id,
                ChannelPool.channel == channel
            ).order_by(ChannelPool.final_rank).all()
            
            keywords = []
            for p in pools:
                kw = self.db.query(Keyword).filter(Keyword.id == p.keyword_id).first()
                if not kw:
                    continue
                ks = self.db.query(KeywordScore).filter(
                    KeywordScore.scoring_run_id == scoring_run_id,
                    KeywordScore.keyword_id == kw.id
                ).first()
                # Intent bilgisini çek (channel filtreli)
                intent_record = self.db.query(IntentAnalysis).filter(
                    IntentAnalysis.scoring_run_id == scoring_run_id,
                    IntentAnalysis.keyword_id == kw.id,
                    IntentAnalysis.is_passed == True,
                    IntentAnalysis.channel == channel
                ).first()

                base_score = None
                if channel == 'ADS':
                    base_score = float(ks.ads_score) if ks and ks.ads_score is not None else None
                elif channel == 'SEO':
                    base_score = float(ks.seo_score) if ks and ks.seo_score is not None else None
                elif channel == 'SOCIAL':
                    base_score = float(ks.social_score) if ks and ks.social_score is not None else None

                vector_similarity = (
                    float(p.relevance_score)
                    if p.relevance_score is not None
                    else relevance_fallback_map.get(kw.id, 1.0)
                )
                if p.adjusted_score is not None:
                    vector_adjusted_score = float(p.adjusted_score)
                elif base_score is not None:
                    vector_adjusted_score = round(base_score * (0.35 + 0.65 * vector_similarity), 4)
                else:
                    vector_adjusted_score = None
                
                keywords.append(KeywordScoreData(
                    keyword_id=kw.id,
                    keyword=kw.keyword,
                    volume=kw.monthly_volume,
                    ads_score=base_score if channel == 'ADS' else None,
                    seo_score=base_score if channel == 'SEO' else None,
                    social_score=base_score if channel == 'SOCIAL' else None,
                    vector_similarity=vector_similarity,
                    vector_adjusted_score=vector_adjusted_score,
                    primary_channel=channel,
                    intent=intent_record.intent_type if intent_record else None
                ))
            
            return ChannelPoolData(channel=channel, keywords=keywords, total=len(keywords))
        
        # Strategic keywords
        strategic_pools = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.is_strategic == True
        ).all()
        
        strategic = []
        for p in strategic_pools:
            kw = self.db.query(Keyword).filter(Keyword.id == p.keyword_id).first()
            if not kw:
                continue
            ks = self.db.query(KeywordScore).filter(
                KeywordScore.scoring_run_id == scoring_run_id,
                KeywordScore.keyword_id == kw.id
            ).first()
            strategic.append(KeywordScoreData(
                keyword_id=kw.id,
                keyword=kw.keyword,
                ads_score=float(ks.ads_score) if ks and ks.ads_score else None,
                seo_score=float(ks.seo_score) if ks and ks.seo_score else None,
                ads_rank=ks.ads_rank if ks else None,
                seo_rank=ks.seo_rank if ks else None
            ))
        
        return ChannelsData(
            ads=get_pool('ADS'),
            seo=get_pool('SEO'),
            social=get_pool('SOCIAL'),
            strategic=strategic
        )
    
    def _collect_seo_contents(self, scoring_run_id: int) -> SEOContentsData:
        """SEO+GEO içeriklerini toplar."""
        # SEO havuzundaki keyword_id'leri bul
        seo_keyword_ids = [
            p.keyword_id
            for p in self.db.query(ChannelPool).filter(
                ChannelPool.scoring_run_id == scoring_run_id,
                ChannelPool.channel == 'SEO'
            ).all()
        ]

        if not seo_keyword_ids:
            return SEOContentsData(contents=[], total=0)

        # Bu keyword'lere ait en güncel SEO içeriklerini getir.
        # content_output_id NULL olsa bile çalışır (ContentOutput.scoring_run_id
        # eski kayıtlarda NULL kalabiliyor).
        contents = (
            self.db.query(SEOGeoContent)
            .filter(SEOGeoContent.keyword_id.in_(seo_keyword_ids))
            .order_by(SEOGeoContent.id.desc())
            .all()
        )

        # Her keyword için yalnızca en son içeriği tut
        seen_keyword_ids: set = set()
        unique_contents = []
        for c in contents:
            if c.keyword_id not in seen_keyword_ids:
                seen_keyword_ids.add(c.keyword_id)
                unique_contents.append(c)
        contents = unique_contents

        if not contents:
            return SEOContentsData(contents=[], total=0)
        
        content_data = []
        for c in contents:
            kw = self.db.query(Keyword).filter(Keyword.id == c.keyword_id).first()
            
            # Compliance results
            seo_result = self.db.query(SEOComplianceResult).filter(
                SEOComplianceResult.seo_geo_content_id == c.id
            ).first()
            
            geo_result = self.db.query(GEOComplianceResult).filter(
                GEOComplianceResult.seo_geo_content_id == c.id
            ).first()
            
            # SEO 11 kriter detayları
            seo_checks = None
            if seo_result:
                seo_checks = {
                    'title_has_keyword': seo_result.title_has_keyword,
                    'title_length_ok': seo_result.title_length_ok,
                    'url_has_keyword': seo_result.url_has_keyword,
                    'intro_keyword_count': seo_result.intro_keyword_count,
                    'word_count_in_range': seo_result.word_count_in_range,
                    'subheading_count_ok': seo_result.subheading_count_ok,
                    'subheadings_have_kw': seo_result.subheadings_have_kw,
                    'has_internal_link': seo_result.has_internal_link,
                    'has_external_link': seo_result.has_external_link,
                    'has_bullet_list': seo_result.has_bullet_list,
                    'sentences_readable': seo_result.sentences_readable,
                    'total_passed': seo_result.total_passed,
                    'improvement_notes': seo_result.improvement_notes,
                }
            
            # GEO 7 kriter detayları
            geo_checks = None
            if geo_result:
                geo_checks = {
                    'intro_answers_question': geo_result.intro_answers_question,
                    'snippet_extractable': geo_result.snippet_extractable,
                    'info_hierarchy_strong': geo_result.info_hierarchy_strong,
                    'tone_is_informative': geo_result.tone_is_informative,
                    'no_fluff_content': geo_result.no_fluff_content,
                    'direct_answer_present': geo_result.direct_answer_present,
                    'has_verifiable_info': geo_result.has_verifiable_info,
                    'total_passed': geo_result.total_passed,
                    'ai_snippet_preview': geo_result.ai_snippet_preview,
                    'improvement_notes': geo_result.improvement_notes,
                }
            
            content_data.append(SEOContentData(
                id=c.id,
                keyword_id=c.keyword_id,
                keyword=kw.keyword if kw else "",
                title=c.title or "",
                url_suggestion=c.url_suggestion,
                intro_paragraph=c.intro_paragraph,
                subheadings=c.subheadings,
                body_sections=c.body_sections,
                body_content=c.body_content,
                bullet_points=c.bullet_points,
                internal_link_anchor=c.internal_link_anchor,
                internal_link_url=c.internal_link_url,
                external_link_anchor=c.external_link_anchor,
                external_link_url=c.external_link_url,
                meta_description=c.meta_description,
                word_count=c.word_count or 0,
                keyword_count=c.keyword_count or 0,
                keyword_density=float(c.keyword_density) if c.keyword_density else None,
                seo_score=float(seo_result.total_score) if seo_result and seo_result.total_score else None,
                geo_score=float(geo_result.total_score) if geo_result and geo_result.total_score else None,
                combined_score=round((float(seo_result.total_score or 0) + float(geo_result.total_score or 0)) / 2, 2) if seo_result or geo_result else None,
                seo_checks=seo_checks,
                geo_checks=geo_checks,
            ))
        
        return SEOContentsData(contents=content_data, total=len(content_data))
    
    def _collect_ads(self, scoring_run_id: int) -> AdsData:
        """Reklam gruplarını toplar."""
        groups = self.db.query(AdGroup).filter(
            AdGroup.scoring_run_id == scoring_run_id
        ).all()
        
        group_data = []
        for g in groups:
            headlines = self.db.query(AdHeadline).filter(
                AdHeadline.ad_group_id == g.id
            ).all()
            
            descriptions = self.db.query(AdDescription).filter(
                AdDescription.ad_group_id == g.id
            ).all()
            
            negatives = self.db.query(NegativeKeyword).filter(
                NegativeKeyword.ad_group_id == g.id
            ).all()
            
            group_data.append(AdGroupData(
                id=g.id,
                group_name=g.group_name,
                group_theme=g.group_theme,
                target_keywords=g.target_keywords or [],
                headlines=[HeadlineData(
                    headline_text=h.headline_text,
                    headline_type=h.headline_type,
                    is_dki=h.is_dki or False
                ) for h in headlines],
                descriptions=[DescriptionData(
                    description_text=d.description_text,
                    description_type=d.description_type
                ) for d in descriptions],
                negative_keywords=[NegativeKeywordData(
                    keyword=n.keyword,
                    match_type=n.match_type,
                    reason=n.reason
                ) for n in negatives]
            ))
        
        return AdsData(ad_groups=group_data, total=len(group_data))
    
    def _collect_social(self, scoring_run_id: int) -> SocialData:
        """Sosyal medya verilerini toplar."""
        categories = self.db.query(SocialCategory).filter(
            SocialCategory.scoring_run_id == scoring_run_id
        ).all()
        
        cat_data = [{
            "id": c.id,
            "name": c.category_name,
            "type": c.category_type,
            "relevance_score": c.relevance_score
        } for c in categories]
        
        # Ideas
        ideas = self.db.query(SocialIdea).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id
        ).all()
        
        idea_data = [{
            "id": i.id,
            "title": i.idea_title,
            "platform": i.target_platform,
            "format": i.content_format,
            "trend_alignment": i.trend_alignment,
            "is_selected": i.is_selected
        } for i in ideas]
        
        # Contents
        contents = self.db.query(SocialContent).join(
            SocialIdea, SocialContent.idea_id == SocialIdea.id
        ).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id
        ).all()
        
        content_data = []
        for c in contents:
            idea = self.db.query(SocialIdea).filter(SocialIdea.id == c.idea_id).first()
            hooks = c.hooks or []
            
            content_data.append(SocialContentData(
                id=c.id,
                idea_title=idea.idea_title if idea else "",
                category_name=None,
                platform=idea.target_platform if idea else "instagram",
                content_format=idea.content_format if idea else "post",
                trend_alignment=idea.trend_alignment if idea else 0.0,
                hooks=[HookData(text=h.get("text", ""), style=h.get("style", "")) for h in hooks],
                caption=c.caption or "",
                scenario=c.scenario,
                hashtags=c.hashtags or [],
                cta_text=c.cta_text
            ))
        
        return SocialData(
            categories=cat_data,
            ideas=idea_data,
            contents=content_data
        )
