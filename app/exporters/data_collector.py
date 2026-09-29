"""
Export Data Collector.

Veritabanından export için veri toplayan merkezi sınıf.
"""
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.compliance.publish_review import (
    checklist_items_from_rows,
    geo_evaluation_source,
    publish_review_from_rows,
)
from app.schemas.export import (
    ExportSectionEnum, FullReport, SummaryData, ScoringData,
    KeywordScoreData, ChannelsData, ChannelPoolData,
    SEOContentsData, SEOContentData, AdsData, AdGroupData,
    HeadlineData, DescriptionData, NegativeKeywordData,
    SocialData, SocialContentData, HookData, BrandProfileData,
    BrandProfileCompetitorData, SocialBriefGroupData, SocialBriefKeywordData
)
from app.database.models import (
    Keyword, ScoringRun, ChannelPool, KeywordScore, IntentAnalysis,
    ContentOutput,
    SEOGeoContent, SEOComplianceResult, GEOComplianceResult,
    AdGroup, AdHeadline, AdDescription, NegativeKeyword,
    SocialCategory, SocialIdea, SocialContent, BrandProfile, KeywordRelevance,
    SocialBrief, SocialBriefKeyword, SocialBriefTarget
)


def collect_run_seo_contents(
    db: Session,
    scoring_run_id: int,
    include_stale_content: bool = False,
) -> list:
    """Bir run'in EXPORT EDILEBILIR SEO iceriklerini secen TEK ortak fonksiyon.

    Sozlesme (plan v4 §1 — SEO run-sizinti duzeltmesi):
    - Yalniz bu run'in SEO havuzundaki keyword'ler
    - Yalniz `ContentOutput.scoring_run_id == run_id` olan kayitlar — ayni
      keyword baska run'da da uretildiyse o iceriklerin SIZMASI engellenir
    - `content_output_id IS NULL` legacy kayitlar DISLANIR (run iliskisi
      kanitlanamaz — guvenli taraf)
    - include_stale_content=False ise stale ContentOutput'lar elenir
    - Keyword basina yalniz EN SON icerik (id desc dedup)

    Collector, summary sayaci ve NO_CONTENT sayaci ayni fonksiyonu kullanir —
    kopyalanmis sorgular zamanla sapmasin.
    """
    seo_keyword_ids = [
        p.keyword_id
        for p in db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'SEO',
        ).all()
    ]
    if not seo_keyword_ids:
        return []

    query = (
        db.query(SEOGeoContent)
        .join(ContentOutput, SEOGeoContent.content_output_id == ContentOutput.id)
        .filter(
            SEOGeoContent.keyword_id.in_(seo_keyword_ids),
            ContentOutput.scoring_run_id == scoring_run_id,
        )
        .order_by(SEOGeoContent.id.desc())
    )
    if not include_stale_content:
        query = query.filter(ContentOutput.is_stale == False)  # noqa: E712

    seen_keyword_ids: set = set()
    unique_contents = []
    for content in query.all():
        if content.keyword_id not in seen_keyword_ids:
            seen_keyword_ids.add(content.keyword_id)
            unique_contents.append(content)
    return unique_contents


def collect_exportable_identity(
    db: Session,
    scoring_run_id: int,
    section: str,
    include_stale_content: bool = False,
) -> frozenset:
    """Bir icerik bolumunun EXPORT EDILEBILIR kayit KIMLIK kumesi.

    Collector'in GERCEK filtre mantigiyla ayni kaynaklardan okur — basit
    `count()` kopyasi degil (kopyalar zamanla sapar, codex tur-3 #4).
    KIMLIK kumesi donmesinin nedeni (codex post-review-2 #1): toplam sayi
    esit kalsa da icerik DEGISMIS olabilir (esit sayida stale+yeni icerik,
    ADS set aktivasyonu); worker'in dosya oncesi/sonrasi karsilastirmasi
    kume esitligiyle yapilir, sayiyla degil.

    - ads:         (generation_set_id, group_id) ciftleri — aktif +
                   non-stale set gruplari VE bagli ContentOutput stale degil;
                   set aktivasyonu grup sayisi ayni kalsa da kumeyi degistirir
    - seo_content: `collect_run_seo_contents` icerik id'leri (run izolasyonu +
                   legacy dislama + stale filtresi + dedup; regenerate YENI
                   satir urettigi icin id yeterli)
    - social:      non-stale kategori→fikir→icerik zinciri + bagli
                   ContentOutput stale degil — (content_id,
                   content_regen_count, idea_regen_count) ucluleri.
                   Regenerate SOCIAL'da YENI SATIR uretmez, ayni ID'yi
                   yerinde gunceller (codex post-review-3 #1) — sayac/ID
                   kumesi degismezdi; regeneration_count'lar kimlige dahil
                   edilerek yerinde guncelleme de yakalanir. Sorgu ORM
                   entity yerine KOLON secer: SQLAlchemy identity-map'i
                   onceki degeri maskeleyemez, degerler DB'den taze okunur.

    `include_stale_content` collector'daki ayni bayraktir — job stale icerigi
    dahil ediyorsa kume de dahil eder (yanlis NO_CONTENT uretilmez).
    """
    if section == 'seo_content':
        return frozenset(
            content.id
            for content in collect_run_seo_contents(
                db, scoring_run_id, include_stale_content
            )
        )
    if section == 'ads':
        from app.generators.ads.generation_sets import active_ad_groups

        query = active_ad_groups(db, scoring_run_id)
        if not include_stale_content:
            query = query.outerjoin(
                ContentOutput, AdGroup.content_output_id == ContentOutput.id
            ).filter(
                (AdGroup.content_output_id.is_(None))
                | (ContentOutput.is_stale == False)  # noqa: E712
            )
        return frozenset((g.generation_set_id, g.id) for g in query.all())
    if section == 'social':
        query = (
            db.query(
                SocialContent.id,
                SocialContent.regeneration_count,
                SocialIdea.regeneration_count,
            )
            .select_from(SocialContent)
            .join(SocialIdea, SocialContent.idea_id == SocialIdea.id)
            .join(SocialCategory, SocialIdea.category_id == SocialCategory.id)
            .filter(
                SocialCategory.scoring_run_id == scoring_run_id,
                SocialCategory.is_stale.is_(False),
                SocialIdea.is_stale.is_(False),
                SocialContent.is_stale.is_(False),
            )
        )
        if not include_stale_content:
            query = query.outerjoin(
                ContentOutput, SocialContent.content_output_id == ContentOutput.id
            ).filter(
                (SocialContent.content_output_id.is_(None))
                | (ContentOutput.is_stale == False)  # noqa: E712
            )
        return frozenset(
            (content_id, content_regen or 0, idea_regen or 0)
            for content_id, content_regen, idea_regen in query.all()
        )
    raise ValueError(f"collect_exportable_identity: bilinmeyen bolum {section!r}")


def count_exportable_content(
    db: Session,
    scoring_run_id: int,
    section: str,
    include_stale_content: bool = False,
) -> int:
    """Bolumun export edilebilir kayit sayisi (plan v4 §5).

    Tek kaynak `collect_exportable_identity` — sayac ile kimlik kumesi
    asla birbirinden sapamaz. create_export pre-check (422 NO_CONTENT)
    bunu kullanir; worker kimlik kumesini dogrudan karsilastirir.
    """
    return len(
        collect_exportable_identity(db, scoring_run_id, section, include_stale_content)
    )


def collect_channel_pool(db: Session, scoring_run_id: int, channel: str) -> List[KeywordScoreData]:
    """Bir kanalin final havuzunu ZENGIN satirlar olarak doner (plan v4 §4).

    Public read-model: hem export collector'i hem kanal-havuz XLSX endpoint'i
    bunu kullanir (API private metot cagirmaz). Alanlar "neden secildi"
    analizine yeter: final_rank, hacim (WK snapshot), kanal skoru,
    vector_similarity, vector_adjusted_score, intent, is_strategic, pool_label.
    """
    relevance_fallback_map = {
        keyword_id: float(relevance_score)
        for keyword_id, relevance_score in (
            db.query(KeywordRelevance.keyword_id, KeywordRelevance.relevance_score)
            .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
            .all()
        )
        if relevance_score is not None
    }

    # Hacim, kanal pools API'siyle AYNI kaynak: workspace snapshot (coklu
    # snapshot'ta en yuksek hacim deterministik secilir)
    run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    wk_volume_map: dict = {}
    if run and run.brand_profile_id:
        from app.database.models import WorkspaceKeyword as _WK

        for wk_kid, wk_vol in (
            db.query(_WK.keyword_id, _WK.monthly_volume)
            .filter(_WK.brand_profile_id == run.brand_profile_id)
            .all()
        ):
            wk_volume_map[wk_kid] = max(wk_volume_map.get(wk_kid, 0), int(wk_vol or 0))

    pools = db.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == scoring_run_id,
        ChannelPool.channel == channel
    ).order_by(ChannelPool.final_rank).all()

    keywords: List[KeywordScoreData] = []
    for p in pools:
        kw = db.query(Keyword).filter(Keyword.id == p.keyword_id).first()
        if not kw:
            continue
        ks = db.query(KeywordScore).filter(
            KeywordScore.scoring_run_id == scoring_run_id,
            KeywordScore.keyword_id == kw.id
        ).first()
        intent_record = db.query(IntentAnalysis).filter(
            IntentAnalysis.scoring_run_id == scoring_run_id,
            IntentAnalysis.keyword_id == kw.id,
            IntentAnalysis.is_passed == True,  # noqa: E712
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
            else relevance_fallback_map.get(kw.id, 0.5)
        )
        if p.adjusted_score is not None:
            vector_adjusted_score = float(p.adjusted_score)
        elif base_score is not None:
            # Motorla ayni formul: adjusted = raw * relevance (coef=1.0);
            # fallback yalnizca adjusted_score'u NULL eski kayitlarda
            vector_adjusted_score = round(base_score * vector_similarity, 4)
        else:
            vector_adjusted_score = None

        keywords.append(KeywordScoreData(
            keyword_id=kw.id,
            keyword=kw.keyword,
            volume=wk_volume_map.get(kw.id, kw.monthly_volume),
            ads_score=base_score if channel == 'ADS' else None,
            seo_score=base_score if channel == 'SEO' else None,
            social_score=base_score if channel == 'SOCIAL' else None,
            vector_similarity=vector_similarity,
            vector_adjusted_score=vector_adjusted_score,
            primary_channel=channel,
            intent=intent_record.intent_type if intent_record else None,
            final_rank=p.final_rank,
            is_strategic=bool(p.is_strategic),
            pool_label=p.pool_label,
        ))

    return keywords


class ExportDataCollector:
    """
    Veritabanından export için veri toplar.

    Her bölüm için ayrı collection metodu vardır.
    Sadece istenen bölümler toplanır.
    """

    def __init__(self, db: Session):
        self.db = db
        self.include_stale_content = False
    
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
        _run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        profile = None
        if _run and _run.brand_profile_id:
            profile = self.db.query(BrandProfile).filter(
                BrandProfile.id == _run.brand_profile_id,
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
            protected_themes=profile_data.get("protected_themes") or [],
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
        
        # Content counts — export edilebilir SEO icerikleri (ortak secim:
        # run izolasyonu + legacy dislama + stale filtresi + dedup)
        seo_content_count = len(collect_run_seo_contents(
            self.db, scoring_run_id, include_stale_content=self.include_stale_content
        ))
        
        # Versiyonlama (Faz E): özet sayacı yalnız AKTİF + NON-STALE seti sayar
        from app.generators.ads.generation_sets import active_ad_groups
        ad_group_count = active_ad_groups(self.db, scoring_run_id).count()
        
        social_content_count = self.db.query(SocialContent).join(
            SocialIdea, SocialContent.idea_id == SocialIdea.id
        ).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id,
            SocialCategory.is_stale.is_(False),
            SocialIdea.is_stale.is_(False),
            SocialContent.is_stale.is_(False),
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
        """Tüm keyword skorlarını toplar.

        Metrikler (hacim/trend/rekabet) skorlamanın GERÇEKTE kullandığı
        WorkspaceKeyword snapshot'ından okunur (score_engine v2 ile aynı
        kaynak). Global Keyword satırı yalnızca metin + workspace'siz eski
        run'lar için fallback'tir — aksi halde Google Ads metrik yenilemesi
        sonrası rapor, skorlamada kullanılandan farklı/bayat değer gösterirdi.
        """
        scores = self.db.query(KeywordScore).filter(
            KeywordScore.scoring_run_id == scoring_run_id
        ).all()

        run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        wk_map = {}
        if run and run.brand_profile_id:
            from app.database.models import WorkspaceKeyword
            wk_map = {
                wk.keyword_id: wk
                for wk in self.db.query(WorkspaceKeyword).filter(
                    WorkspaceKeyword.brand_profile_id == run.brand_profile_id
                )
            }

        keyword_data = []
        for ks in scores:
            kw = self.db.query(Keyword).filter(Keyword.id == ks.keyword_id).first()
            if not kw:
                continue
            metrics_row = wk_map.get(kw.id, kw)
            
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
                volume=metrics_row.monthly_volume,
                trend_3m=float(metrics_row.trend_3m) if metrics_row.trend_3m else None,
                trend_12m=float(metrics_row.trend_12m) if metrics_row.trend_12m else None,
                competition=float(metrics_row.competition_score) if metrics_row.competition_score else None,
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
        """Kanal havuzlarını toplar (public read-model'e delege)."""

        def get_pool(channel: str) -> ChannelPoolData:
            keywords = collect_channel_pool(self.db, scoring_run_id, channel)
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
        """SEO+GEO içeriklerini toplar.

        Seçim TEK ortak fonksiyondan gelir (`collect_run_seo_contents`) —
        run izolasyonu + legacy NULL dışlama + stale filtresi + dedup orada.
        """
        contents = collect_run_seo_contents(
            self.db, scoring_run_id, include_stale_content=self.include_stale_content
        )

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
                faq_items=c.faq_items or [],
                image_alt_texts=c.image_alt_texts or [],
                word_count=c.word_count or 0,
                keyword_count=c.keyword_count or 0,
                keyword_density=float(c.keyword_density) if c.keyword_density else None,
                seo_score=float(seo_result.total_score) if seo_result and seo_result.total_score else None,
                geo_score=float(geo_result.total_score) if geo_result and geo_result.total_score else None,
                # SEO veya GEO kaydindan biri eksikse AttributeError yerine eksik = 0 (eski formul)
                combined_score=round((float(getattr(seo_result, 'total_score', None) or 0) + float(getattr(geo_result, 'total_score', None) or 0)) / 2, 2) if seo_result or geo_result else None,
                seo_checks=seo_checks,
                geo_checks=geo_checks,
                # Ekrandakiyle aynı anlam: eksik/NULL/fallback = değerlendirilmedi
                checklist_items=checklist_items_from_rows(seo_result, geo_result),
                geo_evaluation_source=geo_evaluation_source(geo_result),
                publish_review=publish_review_from_rows(seo_result, geo_result),
            ))

        return SEOContentsData(contents=content_data, total=len(content_data))
    
    def _collect_ads(self, scoring_run_id: int) -> AdsData:
        """Reklam gruplarını toplar.

        Versiyonlama (Faz E): export YALNIZ aktif + non-stale setin
        gruplarını kullanır (ortak active_ad_groups filtresi).
        """
        from app.generators.ads.generation_sets import active_ad_groups

        groups = (
            active_ad_groups(self.db, scoring_run_id)
            .outerjoin(ContentOutput, AdGroup.content_output_id == ContentOutput.id)
        )
        if not self.include_stale_content:
            groups = groups.filter(
                (AdGroup.content_output_id.is_(None)) | (ContentOutput.is_stale == False)
            )
        groups = groups.all()
        
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
        """Sosyal medya verilerini toplar.

        Plan_social_brief_akisi.md §8: rapor brief bazında gruplanır; eski
        (brief_id IS NULL) satırlar "Eski (brief'siz)" grubunda kalır. Ana
        kelime, istenen/gerçek süre, süre durumu ve format_payload
        `content_history_read.py`'deki join kalıbıyla aynı kaynaktan (tek
        sorgu, N+1 yok) okunur.
        """
        categories = self.db.query(SocialCategory).filter(
            SocialCategory.scoring_run_id == scoring_run_id,
            SocialCategory.is_stale.is_(False),
        ).all()

        cat_data = [{
            "id": c.id,
            "name": c.category_name,
            "type": c.category_type,
            "relevance_score": c.relevance_score
        } for c in categories]

        # Ideas — ana kelime + brief hedefinden istenen süre (varsa) tek
        # sorguda (N+1 yok)
        idea_rows = self.db.query(
            SocialIdea,
            Keyword.keyword,
            SocialBriefTarget.duration_min_sec,
            SocialBriefTarget.duration_max_sec,
        ).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).outerjoin(
            Keyword, SocialIdea.keyword_id == Keyword.id
        ).outerjoin(
            SocialBriefTarget, SocialIdea.brief_target_id == SocialBriefTarget.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id,
            SocialCategory.is_stale.is_(False),
            SocialIdea.is_stale.is_(False),
        ).all()

        idea_data = [{
            "id": i.id,
            "title": i.idea_title,
            "platform": i.target_platform,
            "format": i.content_format,
            "trend_alignment": i.trend_alignment,
            "is_selected": i.is_selected,
            "brief_id": i.brief_id,
            "keyword": idea_keyword,
            "duration_min_sec": dur_min,
            "duration_max_sec": dur_max,
        } for i, idea_keyword, dur_min, dur_max in idea_rows]

        # Contents — ana kelime, brief metadatasi, istenen sure ve
        # format_payload tek sorguda; N+1 onlenir (eski kod her icerik icin
        # ayri SocialIdea sorgusu yapiyordu)
        query = self.db.query(
            SocialContent,
            SocialIdea.idea_title,
            SocialIdea.target_platform,
            SocialIdea.content_format,
            SocialIdea.trend_alignment,
            Keyword.keyword,
            SocialBriefTarget.duration_preset_id,
            SocialBriefTarget.duration_min_sec,
            SocialBriefTarget.duration_max_sec,
            SocialBrief.brand_name_snapshot,
            SocialBrief.created_at,
            SocialBrief.is_stale,
        ).select_from(SocialContent).join(
            SocialIdea, SocialContent.idea_id == SocialIdea.id
        ).join(
            SocialCategory, SocialIdea.category_id == SocialCategory.id
        ).outerjoin(
            Keyword, SocialIdea.keyword_id == Keyword.id
        ).outerjoin(
            SocialBriefTarget, SocialIdea.brief_target_id == SocialBriefTarget.id
        ).outerjoin(
            SocialBrief, SocialContent.brief_id == SocialBrief.id
        ).outerjoin(
            ContentOutput, SocialContent.content_output_id == ContentOutput.id
        ).filter(
            SocialCategory.scoring_run_id == scoring_run_id,
            SocialCategory.is_stale.is_(False),
            SocialIdea.is_stale.is_(False),
            SocialContent.is_stale.is_(False),
        )
        if not self.include_stale_content:
            query = query.filter(
                (SocialContent.content_output_id.is_(None)) | (ContentOutput.is_stale == False)  # noqa: E712
            )
        rows = query.all()

        content_data: List[SocialContentData] = []
        groups: Dict[Optional[int], SocialBriefGroupData] = {}

        for (
            c, idea_title, platform, content_format, trend_alignment,
            idea_keyword, dur_preset, dur_min, dur_max,
            brand_name, brief_created_at, brief_is_stale,
        ) in rows:
            hooks = c.hooks or []
            item = SocialContentData(
                id=c.id,
                idea_title=idea_title or "",
                category_name=None,
                platform=platform or "instagram",
                content_format=content_format or "post",
                trend_alignment=trend_alignment if trend_alignment is not None else 0.0,
                hooks=[HookData(text=h.get("text", ""), style=h.get("style", "")) for h in hooks],
                caption=c.caption or "",
                scenario=c.scenario,
                hashtags=c.hashtags or [],
                cta_text=c.cta_text,
                visual_suggestion=c.visual_suggestion,
                video_concept=c.video_concept,
                industry_posting_suggestion=c.industry_posting_suggestion,
                platform_notes=c.platform_notes,
                brief_id=c.brief_id,
                idea_keyword=idea_keyword,
                duration_preset_id=dur_preset,
                duration_min_sec=dur_min,
                duration_max_sec=dur_max,
                actual_duration_sec=c.actual_duration_sec,
                duration_status=c.duration_status,
                validation_warnings=list(c.validation_warnings or []),
                format_payload=c.format_payload,
            )
            content_data.append(item)

            group = groups.get(c.brief_id)
            if group is None:
                if c.brief_id is None:
                    label = "Eski (brief'siz)"
                else:
                    created_label = brief_created_at.strftime('%d.%m.%Y') if brief_created_at else ""
                    brand_part = f" - {brand_name}" if brand_name else ""
                    label = f"Brief #{c.brief_id}{brand_part} ({created_label})" if created_label else f"Brief #{c.brief_id}{brand_part}"
                group = SocialBriefGroupData(
                    brief_id=c.brief_id,
                    label=label,
                    created_at=brief_created_at,
                    is_stale=bool(brief_is_stale) if c.brief_id is not None else False,
                    keywords=[],
                    contents=[],
                )
                groups[c.brief_id] = group
            group.contents.append(item)

        # Brief kelimeleri — yalnız gruplarda geçen brief'ler için tek sorgu
        brief_ids = [bid for bid in groups.keys() if bid is not None]
        if brief_ids:
            bk_rows = self.db.query(SocialBriefKeyword).filter(
                SocialBriefKeyword.brief_id.in_(brief_ids)
            ).order_by(SocialBriefKeyword.brief_id, SocialBriefKeyword.position).all()
            for bk in bk_rows:
                g = groups.get(bk.brief_id)
                if g is not None:
                    g.keywords.append(SocialBriefKeywordData(
                        keyword_id=bk.keyword_id,
                        keyword=bk.keyword_snapshot,
                        position=bk.position,
                    ))

        # Sıralama: brief'ler en yeniden eskiye, "Eski (brief'siz)" grubu
        # (K6 — rapora eklenmeye devam eder) her zaman sonda
        _min_dt = datetime.min.replace(tzinfo=timezone.utc)
        brief_groups = sorted(
            (g for g in groups.values() if g.brief_id is not None),
            key=lambda g: (g.created_at or _min_dt, g.brief_id),
            reverse=True,
        )
        legacy_group = groups.get(None)
        if legacy_group is not None:
            brief_groups.append(legacy_group)

        return SocialData(
            categories=cat_data,
            ideas=idea_data,
            contents=content_data,
            brief_groups=brief_groups,
        )
