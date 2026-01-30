"""
Google Ads content generator.
"""
from typing import List, Dict, Any

from app.generators.ai_service import AIService


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
        
        response = self.ai.complete_json(prompt)
        import json
        return json.loads(response)
    
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
        
        response = self.ai.complete_json(prompt)
        import json
        return json.loads(response)
