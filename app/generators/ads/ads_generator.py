"""
Google Ads content generator.
"""
from typing import List, Dict, Any
import logging
import json

from app.generators.ai_service import AIService

logger = logging.getLogger(__name__)

class AdsGenerator:
    """Google Ads içerik üretici."""
    
    def __init__(self, ai_service: AIService):
        self.ai = ai_service
    
    def generate_responsive_search_ad(
        self,
        keyword: str,
        sector: str = None,
        target_market: str = None
    ) -> Dict[str, Any]:
        """
        Responsive Search Ads (RSA) içeriği üretir.
        
        Returns:
            - headlines: 15 adet başlık (max 30 karakter)
            - descriptions: 4 adet açıklama (max 90 karakter)
            - path1, path2: URL yolları
        """
        prompt = f"""Google Ads için Responsive Search Ad içeriği oluştur:

Anahtar Kelime: {keyword}
Sektör: {sector or 'Genel'}
Hedef Pazar: {target_market or 'TR'}

Kurallar:
- 15 adet başlık (HER BİRİ MUTLAKA 30 KARAKTERİN ALTINDA)
- 4 adet açıklama (HER BİRİ MUTLAKA 90 KARAKTERİN ALTINDA)
- 2 adet URL path önerisi (max 15 karakter)
- Anahtar kelimeyi doğal şekilde dahil et
- CTA (call-to-action) içer

JSON formatında yanıt ver:
{{
  "headlines": ["...", ...],
  "descriptions": ["...", ...],
  "path1": "...",
  "path2": "..."
}}"""
        
        try:
            response = self.ai.complete_json(prompt)
            return json.loads(response)
        except Exception as e:
            logger.error(f"Failed to generate RSA for keyword '{keyword}': {e}")
            # Fallback structure
            return {
                "error": str(e),
                "headlines": [f"Explore {keyword}"],
                "descriptions": [f"Learn more about {keyword} today."],
                "path1": "info",
                "path2": "more"
            }
    
    def generate_ad_extensions(
        self,
        keyword: str,
        sector: str = None
    ) -> Dict[str, Any]:
        """
        Reklam uzantıları üretir.
        
        Returns:
            - sitelinks: 4 adet site bağlantısı
            - callouts: 4 adet callout
            - structured_snippets: Yapılandırılmış snippet
        """
        prompt = f"""Google Ads uzantıları oluştur:

Anahtar Kelime: {keyword}
Sektör: {sector or 'Genel'}

Oluştur:
- 4 adet Sitelink (başlık + açıklama)
- 4 adet Callout (max 25 karakter)
- Structured Snippet (header + values)

JSON formatında yanıt ver."""
        
        try:
            response = self.ai.complete_json(prompt)
            return json.loads(response)
        except Exception as e:
            logger.error(f"Failed to generate ad extensions for keyword '{keyword}': {e}")
            # Fallback structure
            return {
                "error": str(e),
                "sitelinks": [],
                "callouts": [],
                "structured_snippets": {}
            }
