"""
SEO+GEO Content Generator.
Roadmap2.md - Bölüm 6

Tek içerik üretir, hem SEO hem GEO kurallarına uygun.
Sonrasında aynı metin üzerinde iki ayrı kontrol çalıştırılır.
"""
from typing import Dict, Any, List, Optional
from decimal import Decimal
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.generators.ai_service import AIService
from app.generators.seo_geo.prompt_templates import SEO_GEO_GENERATION_PROMPT
from app.compliance.seo_checker import SEOComplianceChecker
# geo_checker lazy import edilir (compliance.__init__ → geo_checker → seo_geo → seo_geo_generator döngüsünü kırar)
from app.database.models import (
    Keyword, ChannelPool, ContentOutput,
    SEOGeoContent, SEOComplianceResult, GEOComplianceResult
)
from app.schemas.seo_geo import (
    SEOGEOGenerateRequest, SEOGEOBulkRequest,
    ContentStructure, SEOGEOContentResponse
)


class SEOGEOGenerator:
    """
    SEO+GEO içerik üretim motoru.
    
    Kullanım:
        generator = SEOGEOGenerator(db, ai_service)
        result = generator.generate_content(request)
    
    Adımlar:
        1. AI ile ham içerik üret
        2. SEO uyumluluk kontrolü (programatik)
        3. GEO uyumluluk kontrolü (AI ile)
        4. Veritabanına kaydet
        5. Sonuç döndür
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        """
        Args:
            db: SQLAlchemy database session
            ai_service: AI servis instance (Gemini, OpenAI, vb.)
        """
        self.db = db
        self.ai_service = ai_service
        self.seo_checker = SEOComplianceChecker()
        from app.compliance.geo_checker import GEOComplianceChecker
        self.geo_checker = GEOComplianceChecker(ai_service)
    
    def generate_content(
        self,
        request: SEOGEOGenerateRequest,
        scoring_run_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Tek anahtar kelime için içerik üretir.
        
        Args:
            request: SEOGEOGenerateRequest
            
        Returns:
            Üretilen içerik ve uyumluluk sonuçları
        """
        # Keyword'ü veritabanından al
        keyword = self.db.query(Keyword).filter(
            Keyword.id == request.keyword_id
        ).first()
        
        if not keyword:
            raise ValueError(f"Keyword not found: {request.keyword_id}")
        
        # Pre-filter context al (varsa)
        prefilter_context = ""
        try:
            from app.core.channel.pre_filters.enricher import PreFilterEnricher
            # scoring_run_id'yi ChannelPool'dan bul
            pool = self.db.query(ChannelPool).filter(
                ChannelPool.keyword_id == keyword.id,
                ChannelPool.channel == 'SEO'
            ).first()
            if pool:
                enricher = PreFilterEnricher(self.db)
                prefilter_context = enricher.build_prompt_context(
                    pool.scoring_run_id, 'SEO', keyword.id
                )
        except Exception:
            pass  # Fail-open

        # Brand context al (onaylı profil varsa)
        brand_context = ""
        try:
            from app.database.models import BrandProfile
            if scoring_run_id:
                brand_profile = self.db.query(BrandProfile).filter(
                    BrandProfile.scoring_run_id == scoring_run_id,
                    BrandProfile.status == "confirmed"
                ).first()
                if brand_profile and brand_profile.profile_data:
                    pd = brand_profile.profile_data
                    parts = []
                    if pd.get("company_name"):
                        parts.append(f"- Marka: {pd['company_name']}")
                    if pd.get("products"):
                        parts.append(f"- Ürünler: {', '.join(pd['products'][:10])}")
                    if pd.get("use_cases"):
                        parts.append(f"- Kullanım Alanları: {', '.join(pd['use_cases'][:5])}")
                    if pd.get("problems_solved"):
                        parts.append(f"- Çözdüğü Sorunlar: {', '.join(pd['problems_solved'][:5])}")
                    if parts:
                        brand_context = "\n# MARKA BAĞLAMI\nBu içerik aşağıdaki marka için yazılmalıdır. İçerikte markanın ürünlerini ve kullanım alanlarını doğal bir şekilde dahil edin.\n" + "\n".join(parts)
        except Exception:
            pass  # Fail-open

        # 1. AI ile ham içerik üret
        raw_content = self._generate_raw_content(
            keyword=keyword.keyword,
            sector=request.sector or keyword.sector or "Genel",
            target_market=request.target_market or keyword.target_market or "Genel",
            tone=request.tone,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max,
            prefilter_context=prefilter_context,
            brand_context=brand_context
        )
        
        # 2. SEO uyumluluk kontrolü (programatik)
        seo_result = self.seo_checker.check(
            content=raw_content,
            keyword=keyword.keyword,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max
        )
        
        # 3. GEO uyumluluk kontrolü (AI ile)
        geo_result = self.geo_checker.check(
            content=raw_content,
            keyword=keyword.keyword
        )
        
        # 4. Veritabanına kaydet
        saved_content = self._save_to_database(
            keyword=keyword,
            content=raw_content,
            seo_result=seo_result,
            geo_result=geo_result,
            scoring_run_id=scoring_run_id
        )
        
        # 5. Combined skor hesapla
        combined_score = (seo_result['score'] + geo_result['score']) / 2
        
        return {
            'id': saved_content.id,
            'keyword_id': keyword.id,
            'keyword': keyword.keyword,
            'content': raw_content,
            'seo_compliance': seo_result,
            'geo_compliance': geo_result,
            'combined_score': round(combined_score, 2),
            'generated_at': saved_content.created_at.isoformat()
        }
    
    def generate_bulk(
        self,
        scoring_run_id: int,
        limit: Optional[int] = None,
        tone: str = "informative"
    ) -> Dict[str, Any]:
        """
        SEO havuzundaki tüm/belirli sayıda kelime için içerik üretir.
        
        Args:
            scoring_run_id: Scoring run ID
            limit: Maksimum üretilecek içerik sayısı (None = tümü)
            tone: İçerik tonu
            
        Returns:
            Toplu üretim özeti
        """
        # SEO havuzundan kelimeleri çek
        query = self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'SEO'
        ).order_by(ChannelPool.final_rank)
        
        if limit:
            query = query.limit(limit)
        
        pool_items = query.all()
        
        if not pool_items:
            return {
                'scoring_run_id': scoring_run_id,
                'total_keywords': 0,
                'generated': 0,
                'failed': 0,
                'results': [],
                'errors': [],
                'average_seo_score': 0,
                'average_geo_score': 0,
                'average_combined_score': 0
            }
        
        results = []
        errors = []
        seo_scores = []
        geo_scores = []
        
        for pool_item in pool_items:
            try:
                request = SEOGEOGenerateRequest(
                    keyword_id=pool_item.keyword_id,
                    tone=tone
                )
                
                result = self.generate_content(request, scoring_run_id=scoring_run_id)
                results.append(result)
                
                seo_scores.append(result['seo_compliance']['score'])
                geo_scores.append(result['geo_compliance']['score'])
                
            except Exception as e:
                keyword = self.db.query(Keyword).filter(
                    Keyword.id == pool_item.keyword_id
                ).first()
                
                errors.append({
                    'keyword_id': pool_item.keyword_id,
                    'keyword': keyword.keyword if keyword else 'Unknown',
                    'error': str(e)
                })
        
        # Ortalama skorları hesapla
        avg_seo = sum(seo_scores) / len(seo_scores) if seo_scores else 0
        avg_geo = sum(geo_scores) / len(geo_scores) if geo_scores else 0
        avg_combined = (avg_seo + avg_geo) / 2 if seo_scores else 0
        
        return {
            'scoring_run_id': scoring_run_id,
            'total_keywords': len(pool_items),
            'generated': len(results),
            'failed': len(errors),
            'results': results,
            'errors': errors,
            'average_seo_score': round(avg_seo, 2),
            'average_geo_score': round(avg_geo, 2),
            'average_combined_score': round(avg_combined, 2)
        }
    
    def _generate_raw_content(
        self,
        keyword: str,
        sector: str,
        target_market: str,
        tone: str,
        word_count_min: int,
        word_count_max: int,
        prefilter_context: str = "",
        brand_context: str = ""
    ) -> Dict[str, Any]:
        """AI ile ham içerik üretir (pre-filter context ve brand context ile zenginleştirilmiş)."""
        prompt = SEO_GEO_GENERATION_PROMPT.format(
            keyword=keyword,
            sector=sector,
            target_market=target_market,
            tone=tone,
            word_count_min=word_count_min,
            word_count_max=word_count_max,
            brand_context=brand_context
        )
        
        # Pre-filter context varsa prompt'a ekle
        if prefilter_context:
            prompt = prompt + prefilter_context
        
        response = self.ai_service.complete_json(prompt, max_tokens=4000)
        content = json.loads(response)
        
        # Temel doğrulama
        required_fields = ['title', 'intro_paragraph', 'subheadings', 'body_sections']
        for field in required_fields:
            if field not in content:
                raise ValueError(f"AI yanıtında '{field}' alanı eksik")
        
        # Varsayılan değerler
        if 'url_suggestion' not in content:
            content['url_suggestion'] = keyword.lower().replace(' ', '-')
        
        if 'bullet_points' not in content:
            content['bullet_points'] = []
        
        if 'word_count' not in content:
            # Kelime sayısını hesapla
            full_text = content['intro_paragraph'] + ' ' + ' '.join(content.get('body_sections', []))
            content['word_count'] = len(full_text.split())
        
        if 'keyword_count' not in content:
            full_text = content['intro_paragraph'] + ' ' + ' '.join(content.get('body_sections', []))
            content['keyword_count'] = full_text.lower().count(keyword.lower())
        
        if 'keyword_density' not in content:
            content['keyword_density'] = round(
                (content['keyword_count'] / content['word_count']) * 100, 2
            ) if content['word_count'] > 0 else 0
        
        return content
    
    def _save_to_database(
        self,
        keyword: Keyword,
        content: Dict[str, Any],
        seo_result: Dict[str, Any],
        geo_result: Dict[str, Any],
        scoring_run_id: Optional[int] = None
    ) -> SEOGeoContent:
        """Üretilen içeriği ve sonuçları DB'ye kaydeder."""
        
        # Ana içerik kaydı
        seo_geo_content = SEOGeoContent(
            keyword_id=keyword.id,
            title=content.get('title', '')[:100],
            url_suggestion=content.get('url_suggestion', '')[:200],
            intro_paragraph=content.get('intro_paragraph', ''),
            body_content='\n\n'.join(content.get('body_sections', [])),
            subheadings=content.get('subheadings', []),
            body_sections=content.get('body_sections', []),
            bullet_points=content.get('bullet_points', []),
            internal_link_anchor=content.get('internal_link_anchor'),
            internal_link_url=content.get('internal_link_suggestion'),
            external_link_anchor=content.get('external_link_anchor'),
            external_link_url=content.get('external_link_url'),
            meta_description=content.get('meta_description', '')[:160],
            word_count=content.get('word_count', 0),
            subheading_count=len(content.get('subheadings', [])),
            keyword_count=content.get('keyword_count', 0),
            keyword_density=Decimal(str(content.get('keyword_density', 0)))
        )
        
        self.db.add(seo_geo_content)
        self.db.flush()  # ID almak için
        
        # SEO uyumluluk kaydı
        seo_compliance = SEOComplianceResult(
            seo_geo_content_id=seo_geo_content.id,
            title_has_keyword=seo_result.get('title_has_keyword', False),
            title_length_ok=seo_result.get('title_length_ok', False),
            url_has_keyword=seo_result.get('url_has_keyword', False),
            intro_keyword_count=seo_result.get('intro_keyword_count', 0),
            word_count_in_range=seo_result.get('word_count_in_range', False),
            subheading_count_ok=seo_result.get('subheading_count_ok', False),
            subheadings_have_kw=seo_result.get('subheadings_have_kw', False),
            has_internal_link=seo_result.get('has_internal_link', False),
            has_external_link=seo_result.get('has_external_link', False),
            has_bullet_list=seo_result.get('has_bullet_list', False),
            sentences_readable=seo_result.get('sentences_readable', False),
            total_passed=seo_result.get('total_passed', 0),
            total_score=Decimal(str(seo_result.get('score', 0))),
            improvement_notes=seo_result.get('improvement_notes', '')
        )
        
        self.db.add(seo_compliance)
        
        # GEO uyumluluk kaydı
        geo_compliance = GEOComplianceResult(
            seo_geo_content_id=seo_geo_content.id,
            intro_answers_question=geo_result.get('intro_answers_question', False),
            snippet_extractable=geo_result.get('snippet_extractable', False),
            info_hierarchy_strong=geo_result.get('info_hierarchy_strong', False),
            tone_is_informative=geo_result.get('tone_is_informative', False),
            no_fluff_content=geo_result.get('no_fluff_content', False),
            direct_answer_present=geo_result.get('direct_answer_present', False),
            has_verifiable_info=geo_result.get('has_verifiable_info', False),
            total_passed=geo_result.get('total_passed', 0),
            total_score=Decimal(str(geo_result.get('score', 0))),
            ai_snippet_preview=geo_result.get('ai_snippet_preview', '')[:500] if geo_result.get('ai_snippet_preview') else None,
            improvement_notes=geo_result.get('improvement_notes', '')
        )
        
        self.db.add(geo_compliance)
        
        # ContentOutput tablosuna da kaydet (eski sistemle uyumluluk için)
        content_output = ContentOutput(
            keyword_id=keyword.id,
            scoring_run_id=scoring_run_id,  # artık NULL değil
            channel='SEO',
            content_type='seo_geo_blog',
            content_data=content,
            seo_compliance_score=Decimal(str(seo_result.get('score', 0))),
            geo_compliance_score=Decimal(str(geo_result.get('score', 0)))
        )
        
        self.db.add(content_output)
        
        # Link seo_geo_content to content_output
        self.db.flush()
        seo_geo_content.content_output_id = content_output.id
        
        self.db.commit()
        
        return seo_geo_content


# Legacy alias
SEOGeoGenerator = SEOGEOGenerator
