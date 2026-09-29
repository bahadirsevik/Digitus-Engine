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

from loguru import logger
from sqlalchemy.orm import Session

from app.core.channel.ai_json import parse_ai_json_object, AIJsonParseError

from app.generators.ai_service import AIService, scoped
from app.generators.seo_geo.prompt_templates import (
    SEO_GEO_GENERATION_PROMPT, SEO_GEO_REVISION_PROMPT,
    SEO_GEO_EXPANSION_PROMPT,
)
from app.generators.seo_geo.content_quality import (
    build_internal_link_pool, build_link_pool_context,
    validate_internal_link, validate_external_link,
    filter_brand_items_by_keyword,
)
from app.compliance.seo_checker import SEOComplianceChecker
from app.compliance.publish_review import publish_review_from_results
# geo_checker lazy import edilir (compliance.__init__ → geo_checker → seo_geo → seo_geo_generator döngüsünü kırar)
from app.database.models import (
    Keyword, ChannelPool, ContentOutput,
    SEOGeoContent, SEOComplianceResult, GEOComplianceResult,
    ScoringRun,
)
from app.schemas.seo_geo import (
    SEOGEOGenerateRequest, SEOGEOBulkRequest,
    ContentStructure, SEOGEOContentResponse
)

# 500-600 kelimelik makale + checklist alanlari (FAQ/alt-text) icin cikti
# tavani. Run-16 olcumu: 6000'lik tavanda dusunme token'lari 4.5-5.7k yiyip
# makaleyi kesiyordu (icerik = artakalan butceye sigan kadardi). Tavan
# maliyetsizdir (yalniz kullanilan token faturalanir) — genis tutulur.
SEO_GEO_MAX_TOKENS = 12000


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
        self.ai_service = scoped(ai_service, "seo_generation")
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

        # Brand context al (onaylı profil varsa).
        # Konu dışı sızıntıyı önlemek için profil listeleri keyword'e göre
        # filtrelenir: saç tarağı yazısına diş fırçası ürünleri girmez.
        brand_context = ""
        # P1.1: sektör/hedef kitle CONFIRMED profilden okunur. Önceden yalnız
        # request veya Keyword'ün LEGACY kolonlarından geliyordu; o kolonlar
        # hiçbir CSV'den doldurulmadığı için (models.py "LEGACY",
        # google_ads_parser hiç atamıyor) prompt pratikte "Genel/Genel" ile
        # koşuyordu. Fail-open: profil yoksa boş kalır, zincir legacy'ye düşer.
        profile_sector = ""
        profile_audience = ""
        link_pool = []
        try:
            from app.database.models import BrandProfile
            if scoring_run_id:
                _sr = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
                brand_profile = None
                if _sr and _sr.brand_profile_id:
                    brand_profile = self.db.query(BrandProfile).filter(
                        BrandProfile.id == _sr.brand_profile_id,
                        BrandProfile.status == "confirmed",
                        BrandProfile.deleted_at.is_(None),
                    ).first()
                if brand_profile and brand_profile.profile_data:
                    pd = brand_profile.profile_data
                    kw_text = keyword.keyword
                    profile_sector = str(pd.get("sector") or "").strip()
                    profile_audience = str(pd.get("target_audience") or "").strip()
                    products = filter_brand_items_by_keyword(kw_text, pd.get("products"))[:10]
                    # P1.1: `services` marka bağlamına HİÇ girmiyordu — ADS ve
                    # SOCIAL bu alanı product_facts üzerinden görürken yalnız
                    # SEO'da unutulmuştu. Ürünlerle AYNI keyword filtresinden
                    # geçer: konu dışı hizmet sızıntısı olmasın.
                    services = filter_brand_items_by_keyword(kw_text, pd.get("services"))[:10]
                    use_cases = filter_brand_items_by_keyword(kw_text, pd.get("use_cases"))[:5]
                    problems = filter_brand_items_by_keyword(kw_text, pd.get("problems_solved"))[:5]
                    parts = []
                    if pd.get("company_name"):
                        parts.append(f"- Marka: {pd['company_name']}")
                    if products:
                        parts.append(f"- Ürünler: {', '.join(products)}")
                    if services:
                        parts.append(f"- Hizmetler: {', '.join(services)}")
                    if use_cases:
                        parts.append(f"- Kullanım Alanları: {', '.join(use_cases)}")
                    if problems:
                        parts.append(f"- Çözdüğü Sorunlar: {', '.join(problems)}")
                    if parts:
                        brand_context = (
                            "\n# MARKA BAĞLAMI\n"
                            "Bu içerik aşağıdaki marka için yazılmalıdır. YALNIZCA aşağıda "
                            "listelenen, konuyla ilgili ürün ve kullanım alanlarından bahset; "
                            "markanın burada listelenmeyen diğer ürün kategorilerine değinme.\n"
                            + "\n".join(parts)
                        )
                if brand_profile:
                    link_pool = build_internal_link_pool(brand_profile)
        except Exception:
            pass  # Fail-open

        # 1. AI ile ham içerik üret
        raw_content = self._generate_raw_content(
            keyword=keyword.keyword,
            # P1.1 fallback zinciri: açık request > CONFIRMED profil >
            # Keyword LEGACY kolonu > "Genel". Profil adımı ORTADA durur —
            # request bir override'dır ve profili ezmeye devam eder.
            sector=(
                request.sector or profile_sector or keyword.sector or "Genel"
            ),
            target_market=(
                request.target_market
                or profile_audience
                or keyword.target_market
                or "Genel"
            ),
            tone=request.tone,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max,
            prefilter_context=prefilter_context,
            brand_context=brand_context,
            link_pool_context=build_link_pool_context(link_pool)
        )

        # 1b. Link dogrulama: AI'nin sectigi internal link gercek site
        # URL'lerinden biri degilse havuzdaki gercek URL ile degistirilir;
        # gecersiz/uydurma external link temizlenir.
        raw_content = validate_internal_link(raw_content, link_pool)
        raw_content = validate_external_link(raw_content)

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

        # 3b. TEK turluk hedefli revizyon: basarisiz kriter varsa yalnizca
        # onlari duzeltmesi istenir; skor gerilerse orijinal icerik korunur.
        raw_content, seo_result, geo_result = self._maybe_revise_once(
            keyword=keyword.keyword,
            content=raw_content,
            seo_result=seo_result,
            geo_result=geo_result,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max,
            link_pool=link_pool,
        )

        # 3c. TEK turluk hedefli GENISLETME: kelime sayisi hala min altindaysa
        # salt-ekleme sozlesmesiyle uzatilir (mevcut metin korunur); denetci
        # regresyon guard'i sulandirmayi engeller — skor gerilerse orijinal kalir.
        raw_content, seo_result, geo_result = self._maybe_expand_once(
            keyword=keyword.keyword,
            content=raw_content,
            seo_result=seo_result,
            geo_result=geo_result,
            word_count_min=request.word_count_min,
            word_count_max=request.word_count_max,
            link_pool=link_pool,
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
            # Kritik checklist maddesi başarısızsa "yayın öncesi inceleme
            # gerekli" — yalnız etiket; üretim başarısız sayılmaz, ek tur yok
            'publish_review': publish_review_from_results(seo_result, geo_result),
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
    
    @staticmethod
    def _parse_content_json(response: str, required_fields: List[str]) -> Dict[str, Any]:
        """AI yanitini dayanikli sekilde tek icerik objesine cevirir.

        Once duz json.loads; bozuk yanitlar icin ai_json kurtarma zinciri
        (fence/akilli tirnak/trailing comma/dengesiz parantez/kismi obje).
        Zorunlu alanlari tasiyan ilk obje secilir.
        """
        # Ortak kurtarma zinciri (ai_json) — sozlesme: zorunlu alan eksikse
        # kurtarilmis SAYILMAZ, hata atilir
        return parse_ai_json_object(response, required_fields)

    def _generate_raw_content(
        self,
        keyword: str,
        sector: str,
        target_market: str,
        tone: str,
        word_count_min: int,
        word_count_max: int,
        prefilter_context: str = "",
        brand_context: str = "",
        link_pool_context: str = ""
    ) -> Dict[str, Any]:
        """AI ile ham içerik üretir (pre-filter context ve brand context ile zenginleştirilmiş)."""
        # Kelime bütçesi bölümlere kırılır: modeller global "500-600 yaz"
        # talimatına uymuyor (run-16/17: temiz STOP'ta bile ~410 kelime) ama
        # bölüm başına küçük hedefleri tutturuyor. İçerik baştan doğru
        # uzunlukta PLANLANIR — sonradan esnetilmez (anlam riski en düşük yol).
        intro_words = 70
        section_count = 5
        section_words_min = max(60, (word_count_min - intro_words) // section_count)
        section_words_max = max(
            section_words_min + 15, (word_count_max - intro_words) // section_count
        )
        prompt = SEO_GEO_GENERATION_PROMPT.format(
            keyword=keyword,
            sector=sector,
            target_market=target_market,
            tone=tone,
            word_count_min=word_count_min,
            word_count_max=word_count_max,
            intro_words=intro_words,
            section_count=section_count,
            section_words_min=section_words_min,
            section_words_max=section_words_max,
            current_year=datetime.now().year,
            brand_context=brand_context,
            link_pool_context=link_pool_context
        )

        # Pre-filter context varsa prompt'a ekle
        if prefilter_context:
            prompt = prompt + prefilter_context

        required_fields = ['title', 'intro_paragraph', 'subheadings', 'body_sections']

        # Gemini bozuk JSON dondurebilir (trailing comma, fence, truncation).
        # Once dayanikli parse denenir; o da tutmazsa BIR kez yeniden uretilir.
        # max_tokens 6000: 500-600 kelime + FAQ/alt-text alanlari (checklist)
        response = self.ai_service.complete_json(prompt, max_tokens=SEO_GEO_MAX_TOKENS)
        try:
            content = self._parse_content_json(response, required_fields)
        except (ValueError, AIJsonParseError) as first_error:
            logger.warning(
                f"SEO icerik JSON parse edilemedi, yeniden deneniyor | keyword={keyword} | {first_error}"
            )
            response = self.ai_service.complete_json(prompt, max_tokens=SEO_GEO_MAX_TOKENS)
            content = self._parse_content_json(response, required_fields)
        
        # Varsayılan değerler
        if 'url_suggestion' not in content:
            content['url_suggestion'] = keyword.lower().replace(' ', '-')

        if 'bullet_points' not in content:
            content['bullet_points'] = []

        # Kelime istatistikleri AI beyanina GUVENILMEZ — her zaman gercek
        # metinden yeniden hesaplanir (codex: canli #19 icerikte AI 578 dedi,
        # gercek govde 429 idi; compliance yanlis pass vermisti)
        self._recompute_word_stats(content, keyword)

        return content

    @staticmethod
    def _recompute_word_stats(content: Dict[str, Any], keyword: str) -> None:
        """word_count / keyword_count / keyword_density'yi GERCEK metinden
        (intro + body_sections) hesaplayip content'e yazar; AI'nin beyan
        ettigi degerler her durumda ezilir."""
        full_text = (
            content.get('intro_paragraph', '')
            + ' '
            + ' '.join(
                s if isinstance(s, str) else str(s)
                for s in content.get('body_sections', [])
            )
        )
        word_count = len(full_text.split())
        keyword_count = full_text.lower().count(keyword.lower())
        content['word_count'] = word_count
        content['keyword_count'] = keyword_count
        content['keyword_density'] = (
            round((keyword_count / word_count) * 100, 2) if word_count > 0 else 0
        )
    
    # ── Hedefli revizyon (tek tur) ────────────────────────────────

    # SEO checker'in boolean alanlari → insan-okur kriter aciklamasi
    _SEO_CRITERIA_TR = {
        'title_has_keyword': "Başlık anahtar kelimeyi içermiyor",
        'title_length_ok': "Başlık 70 karakterden uzun",
        'url_has_keyword': "URL önerisinde keyword yok",
        'word_count_in_range': "Kelime sayısı hedef aralığın dışında",
        'subheading_count_ok': "3'ten az alt başlık var",
        'subheadings_have_kw': "Hiçbir alt başlıkta keyword geçmiyor",
        'has_internal_link': "Internal link eksik",
        'has_external_link': "External link eksik",
        'has_bullet_list': "Madde işaretli liste eksik",
        'sentences_readable': "Ortalama cümle uzunluğu 20 kelimeyi aşıyor",
        # Şirket checklist kriterleri (P100-P60)
        'paragraphs_short': "Paragraflar çok uzun (2-3 cümle olmalı, max 5)",
        'subheadings_are_questions': "Alt başlıkların en az üçte ikisi soru formatında olmalı",
        'has_faq_items': (
            "faq_items eksik/kusurlu (3-5 çift; soru ve cevap boş olmamalı, "
            "cevap 1-2 cümle)"
        ),
        'has_steps_or_table': "Adım adım anlatım veya karşılaştırma tablosu eksik",
        'mentions_current_year': "Güncel yıl metinde geçmiyor",
        'has_alt_texts': (
            "image_alt_texts eksik/kusurlu (2-3 boş olmayan, anahtar kelimeyi "
            "içeren açıklayıcı öneri)"
        ),
    }
    _GEO_CRITERIA_TR = {
        'intro_answers_question': "Giriş paragrafı ana soruya doğrudan yanıt vermiyor",
        'snippet_extractable': "Giriş paragrafı bağımsız alıntılanabilir değil",
        'info_hierarchy_strong': (
            "İlk ~200 kelime konuyu özetlemiyor / Özet → Detay → Örnek hiyerarşisi zayıf"
        ),
        'tone_is_informative': "Ton bilgilendirici/tarafsız değil",
        'no_fluff_content': "Dolgu/tekrar eden cümleler var",
        'direct_answer_present': "İlk 40-60 kelimede cevap yok",
        'has_verifiable_info': "Doğrulanabilir bilgi eksik",
    }

    def _collect_failed_criteria(
        self, seo_result: Dict[str, Any], geo_result: Dict[str, Any]
    ) -> List[str]:
        failed = [
            f"[SEO] {desc}" for key, desc in self._SEO_CRITERIA_TR.items()
            if seo_result.get(key) is False
        ]
        if seo_result.get('intro_keyword_count', 2) < 2:
            failed.append("[SEO] Giriş paragrafında keyword 2 kereden az geçiyor")
        # Güvence (codex): haritada olmayan/ileride eklenecek kriterler de
        # checks listesinden süpürülür — revizyon prompt'una tam akar
        seen_keys = set(self._SEO_CRITERIA_TR) | {'intro_keyword_count'}
        for item in seo_result.get('checks', []) or []:
            criterion = item.get('criterion')
            if item.get('status') == 'fail' and criterion not in seen_keys:
                detail = item.get('details') or criterion
                failed.append(f"[SEO] {criterion}: {detail}")
                seen_keys.add(criterion)
        # GEO AI değerlendirmesi yapılamadıysa (fallback) kriterler "kaldı"
        # değil "değerlendirilmedi"dir — revizyon hedefi yapılmaz
        if geo_result.get('evaluation_source') != 'fallback':
            failed.extend(
                f"[GEO] {desc}" for key, desc in self._GEO_CRITERIA_TR.items()
                if geo_result.get(key) is False
            )
        return failed

    def _maybe_revise_once(
        self,
        keyword: str,
        content: Dict[str, Any],
        seo_result: Dict[str, Any],
        geo_result: Dict[str, Any],
        word_count_min: int,
        word_count_max: int,
        link_pool: List[Dict[str, str]],
    ):
        """Basarisiz kriter varsa TEK hedefli revizyon turu calistirir.

        Revize icerik yeniden kontrol edilir; combined skor orijinalin
        altina duserse orijinal korunur (regresyon guard'i).
        """
        failed = self._collect_failed_criteria(seo_result, geo_result)
        if not failed:
            return content, seo_result, geo_result

        notes = " | ".join(filter(None, [
            seo_result.get('improvement_notes', ''),
            geo_result.get('improvement_notes', ''),
        ])) or "-"

        try:
            prompt = SEO_GEO_REVISION_PROMPT.format(
                keyword=keyword,
                content_json=json.dumps(content, ensure_ascii=False),
                failed_criteria="\n".join(f"- {f}" for f in failed),
                improvement_notes=notes,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                current_year=datetime.now().year,
            )
            response = self.ai_service.complete_json(prompt, max_tokens=SEO_GEO_MAX_TOKENS)
            revised = self._parse_content_json(
                response, ['title', 'intro_paragraph', 'subheadings', 'body_sections']
            )
        except Exception:
            # Revizyon basarisizsa orijinal icerikle devam et (fail-open)
            return content, seo_result, geo_result

        # Linkler revizyonda degistirilmemeli — yine de dogrula
        revised = validate_internal_link(revised, link_pool)
        revised = validate_external_link(revised)
        # Revize metnin kelime istatistikleri de gercek metinden (AI beyani ezilir)
        self._recompute_word_stats(revised, keyword)

        seo2 = self.seo_checker.check(
            content=revised, keyword=keyword,
            word_count_min=word_count_min, word_count_max=word_count_max,
        )
        geo2 = self.geo_checker.check(content=revised, keyword=keyword)

        if (seo2['score'] + geo2['score']) >= (seo_result['score'] + geo_result['score']):
            return revised, seo2, geo2
        return content, seo_result, geo_result

    def _maybe_expand_once(
        self,
        keyword: str,
        content: Dict[str, Any],
        seo_result: Dict[str, Any],
        geo_result: Dict[str, Any],
        word_count_min: int,
        word_count_max: int,
        link_pool: List[Dict[str, str]],
    ):
        """Kelime sayisi min altindaysa TEK salt-ekleme genisletme turu.

        Anlam kaybi korumasi: prompt mevcut metni kelimesi kelimesine korur,
        yalnizca yeni paragraf/bolum ekler. Sulandirma korumasi: genisletilmis
        icerik SEO+GEO denetcilerinden yeniden gecer; birlesik skor orijinalin
        altina duserse (or. GEO 'dolgu yok' kriteri) orijinal korunur. Gercekten
        uzamamis (esit/kisa) sonuc da reddedilir. En kotu senaryo = mevcut durum.
        """
        current_words = int(content.get('word_count') or 0)
        if current_words >= word_count_min:
            return content, seo_result, geo_result

        try:
            # Hedefin ortasına nişan al — "500-600'e çıkar" serbestisi canlıda
            # +229 kelimelik aşım üretti (683/600); eklenecek miktar AÇIKÇA verilir
            target_mid = (word_count_min + word_count_max) // 2
            prompt = SEO_GEO_EXPANSION_PROMPT.format(
                keyword=keyword,
                content_json=json.dumps(content, ensure_ascii=False),
                current_words=current_words,
                words_to_add=max(target_mid - current_words, 0),
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                current_year=datetime.now().year,
            )
            response = self.ai_service.complete_json(
                prompt, max_tokens=SEO_GEO_MAX_TOKENS
            )
            expanded = self._parse_content_json(
                response, ['title', 'intro_paragraph', 'subheadings', 'body_sections']
            )
        except Exception as e:
            # Genisletme basarisizsa orijinal icerikle devam (fail-open)
            logger.warning(f"Genisletme turu basarisiz, orijinal korunuyor: {e}")
            return content, seo_result, geo_result

        expanded = validate_internal_link(expanded, link_pool)
        expanded = validate_external_link(expanded)
        self._recompute_word_stats(expanded, keyword)

        new_words = int(expanded.get('word_count') or 0)
        if new_words <= current_words:
            logger.warning(
                f"Genisletme icerigi uzatmadi ({current_words}→{new_words}), "
                f"orijinal korunuyor"
            )
            return content, seo_result, geo_result

        seo2 = self.seo_checker.check(
            content=expanded, keyword=keyword,
            word_count_min=word_count_min, word_count_max=word_count_max,
        )
        geo2 = self.geo_checker.check(content=expanded, keyword=keyword)

        if (seo2['score'] + geo2['score']) >= (seo_result['score'] + geo_result['score']):
            logger.info(
                f"SEO icerik genisletildi: {current_words}→{new_words} kelime "
                f"(hedef {word_count_min}-{word_count_max})"
            )
            return expanded, seo2, geo2

        logger.warning(
            f"Genisletme skoru geriletti "
            f"({seo_result['score']}+{geo_result['score']} → "
            f"{seo2['score']}+{geo2['score']}), orijinal korunuyor"
        )
        return content, seo_result, geo_result

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
            faq_items=content.get('faq_items') or [],
            image_alt_texts=content.get('image_alt_texts') or [],
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
            improvement_notes=seo_result.get('improvement_notes', ''),
            # Checklist kriterleri dahil TUM kriter listesi kalici kayit (codex)
            checks_json=seo_result.get('checks') or []
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
