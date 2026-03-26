"""
Brand Profile Extractor.
Uses AI to analyze crawled website content and generate a structured brand profile.
"""
import json
import logging
from typing import Dict, Any, Optional, List

from app.generators.ai_service import AIService
from app.core.site_analyzer.crawler import SiteCrawler

logger = logging.getLogger(__name__)

# Max chars to send to AI per site
MAX_AI_INPUT_CHARS = 8000

PROFILE_EXTRACTION_PROMPT = """
# ROL
Sen bir dijital pazarlama ve marka analiz uzmanısın.

# GÖREV
Aşağıda bir firmanın web sitesinden alınmış sayfa içerikleri var.
Bu içeriklerden firmanın detaylı profilini çıkar.

# SİTE İÇERİKLERİ
{site_content}

# ÇIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "company_name": "Firma adı",
    "sector": "Ana sektör (kısa, 2-5 kelime)",
    "products": ["ürün1", "ürün2", "..."],
    "services": ["hizmet1", "..."],
    "target_audience": "Hedef kitle tanımı",
    "use_cases": ["kullanım senaryosu 1", "..."],
    "problems_solved": ["çözülen problem 1", "..."],
    "brand_terms": ["marka adı", "marka + ürün varyasyonları", "..."],
    "exclude_themes": ["firmanın SATMADIĞI/İLGİSİZ konular", "..."]
}}

# KURALLAR
- products: Firmanın gerçekten sattığı/ürettiği ürünleri yaz
- use_cases: Bu ürünlerin nasıl kullanıldığını yaz
- problems_solved: Bu ürünlerin hangi sorunları çözdüğünü yaz
- brand_terms: Marka adı + ürün kombinasyonlarını yaz (arama terimi olarak)
- exclude_themes: Sitede OLMADIĞI halde karıştırılabilecek konuları yaz
- Türkçe yaz
- Tahmin yapma, sadece sitede gördüğün bilgileri kullan
"""

COMPETITOR_VALIDATION_PROMPT = """
# GÖREV
Ana firma profili ile rakip firma profillerini karşılaştır.
Ana firmanın profil doğruluğunu doğrula.

# ANA FİRMA PROFİLİ
{main_profile}

# RAKİP FİRMA PROFİLLERİ
{competitor_profiles}

# ÇIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "consistency_score": 0.85,
    "competitors": [
        {{
            "url": "rakip url",
            "status": "same_sector|nearby|mismatch",
            "summary": "1-2 cümle açıklama"
        }}
    ],
    "warnings": ["Uyarı mesajları varsa"],
    "profile_adjustments": ["Profilde düzeltilmesi gereken noktalar varsa"]
}}
"""


class ProfileExtractor:
    """Extracts structured brand profile from crawled website content."""

    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
        self.crawler = SiteCrawler()

    def extract_profile(self, company_url: str) -> Dict[str, Any]:
        """
        Crawl site and extract brand profile.

        Returns:
            {
                "profile": {...},
                "source_pages": [...],
                "anchor_texts": [...],
                "error": None
            }
        """
        # Crawl the site
        crawl_result = self.crawler.crawl_site(company_url)

        if crawl_result["error"] and not crawl_result["pages"]:
            return {
                "profile": None,
                "source_pages": [],
                "anchor_texts": [],
                "error": crawl_result["error"],
            }

        # Prepare content for AI
        site_content = self._prepare_content(crawl_result["pages"])

        # Extract profile via AI
        try:
            prompt = PROFILE_EXTRACTION_PROMPT.format(site_content=site_content)
            response = self.ai_service.complete_json(prompt, max_tokens=2000)
            profile = json.loads(response)
        except Exception as e:
            logger.error(f"Profile extraction AI error: {e}")
            return {
                "profile": None,
                "source_pages": self._page_summaries(crawl_result["pages"]),
                "anchor_texts": [],
                "error": f"AI extraction failed: {e}",
            }

        # Generate anchor texts from profile
        anchor_texts = self._generate_anchors(profile)
        profile["anchor_texts"] = anchor_texts

        return {
            "profile": profile,
            "source_pages": self._page_summaries(crawl_result["pages"]),
            "anchor_texts": anchor_texts,
            "error": None,
        }

    def validate_with_competitors(
        self,
        main_profile: Dict,
        competitor_urls: List[str],
    ) -> Dict[str, Any]:
        """
        Validate main profile against competitor sites.

        Returns validation result with consistency score.
        """
        competitor_profiles = []

        for url in competitor_urls[:3]:  # Max 3 competitors
            try:
                crawl_result = self.crawler.crawl_site(url)
                if crawl_result["pages"]:
                    content = self._prepare_content(
                        crawl_result["pages"][:3]  # Fewer pages per competitor
                    )
                    competitor_profiles.append({
                        "url": url,
                        "content_summary": content[:2000],
                    })
            except Exception as e:
                logger.warning(f"Competitor crawl failed for {url}: {e}")
                competitor_profiles.append({
                    "url": url,
                    "content_summary": f"Crawl failed: {e}",
                })

        if not competitor_profiles:
            return {
                "consistency_score": None,
                "competitors": [],
                "warnings": ["Hiçbir rakip site crawl edilemedi"],
                "profile_adjustments": [],
            }

        try:
            prompt = COMPETITOR_VALIDATION_PROMPT.format(
                main_profile=json.dumps(main_profile, ensure_ascii=False, indent=2),
                competitor_profiles=json.dumps(
                    competitor_profiles, ensure_ascii=False, indent=2
                ),
            )
            response = self.ai_service.complete_json(prompt, max_tokens=1500)
            return json.loads(response)
        except Exception as e:
            logger.error(f"Competitor validation AI error: {e}")
            return {
                "consistency_score": None,
                "competitors": [],
                "warnings": [f"Doğrulama AI hatası: {e}"],
                "profile_adjustments": [],
            }

    def _prepare_content(self, pages: List[Dict]) -> str:
        """Prepare crawled pages into a single text for AI."""
        parts = []
        total_chars = 0

        for page in pages:
            text = page.get("text", "")
            title = page.get("title", "")
            url = page.get("url", "")

            section = f"## Sayfa: {title}\nURL: {url}\n\n{text}"

            if total_chars + len(section) > MAX_AI_INPUT_CHARS:
                remaining = MAX_AI_INPUT_CHARS - total_chars
                if remaining > 200:
                    parts.append(section[:remaining])
                break

            parts.append(section)
            total_chars += len(section)

        return "\n\n---\n\n".join(parts)

    def _page_summaries(self, pages: List[Dict]) -> List[Dict]:
        """Create lightweight page summaries for storage."""
        return [
            {
                "url": p.get("url", ""),
                "title": p.get("title", ""),
                "status": p.get("status", 0),
            }
            for p in pages
        ]

    def _generate_anchors(self, profile: Dict) -> List[str]:
        """
        Generate embedding anchor texts from profile.
        Each anchor represents a different semantic category.
        """
        anchors = []

        # Products anchor
        products = profile.get("products", [])
        if products:
            anchors.append(" ".join(products))

        # Use cases anchor
        use_cases = profile.get("use_cases", [])
        if use_cases:
            anchors.append(" ".join(use_cases))

        # Problems solved anchor
        problems = profile.get("problems_solved", [])
        if problems:
            anchors.append(" ".join(problems))

        # Brand terms anchor
        brand_terms = profile.get("brand_terms", [])
        if brand_terms:
            anchors.append(" ".join(brand_terms))

        # Combined sector + target audience anchor
        sector = profile.get("sector", "")
        audience = profile.get("target_audience", "")
        if sector or audience:
            anchors.append(f"{sector} {audience}".strip())

        return anchors
