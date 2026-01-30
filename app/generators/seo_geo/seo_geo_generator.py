"""
SEO+GEO optimized content generator.
"""
from typing import Dict, Any, List

from app.generators.ai_service import AIService


class SEOGeoGenerator:
    """SEO ve GEO uyumlu içerik üretici."""
    
    def __init__(self, ai_service: AIService):
        self.ai = ai_service
    
    def generate_blog_post(
        self,
        keyword: str,
        target_word_count: int = 1500,
        sector: str = None,
        location: str = None
    ) -> Dict[str, Any]:
        """
        SEO uyumlu blog yazısı üretir.
        
        Returns:
            - title: SEO başlığı
            - meta_description: Meta açıklama
            - h1: Ana başlık
            - sections: İçerik bölümleri
            - schema_markup: JSON-LD schema
        """
        geo_context = f"\nKonum/Bölge: {location}" if location else ""
        
        prompt = f"""SEO uyumlu blog içeriği oluştur:

Anahtar Kelime: {keyword}
Sektör: {sector or 'Genel'}
Hedef Kelime Sayısı: {target_word_count}{geo_context}

SEO Kuralları:
- Anahtar kelime title'da, H1'de, ilk paragrafta yer almalı
- Meta description 150-160 karakter
- Her bölümde H2/H3 başlıklar
- Kelime yoğunluğu %1-2
- Internal/external link önerileri

GEO Kuralları (varsa):
- Yerel referanslar
- Lokasyon bazlı anahtar kelimeler

JSON formatında yanıt ver:
{{
  "title": "...",
  "meta_description": "...",
  "h1": "...",
  "sections": [
    {{"heading": "H2 başlık", "content": "..."}},
    ...
  ],
  "schema_markup": {{...}},
  "seo_score_estimate": 0.85,
  "geo_score_estimate": 0.80
}}"""
        
        response = self.ai.complete_json(prompt, max_tokens=4000)
        import json
        return json.loads(response)
    
    def generate_landing_page(
        self,
        keyword: str,
        cta_type: str = "contact"
    ) -> Dict[str, Any]:
        """Landing page içeriği üretir."""
        prompt = f"""Dönüşüm odaklı landing page içeriği oluştur:

Anahtar Kelime: {keyword}
CTA Tipi: {cta_type}

Bölümler:
- Hero section (başlık + alt başlık)
- Problem/Solution
- Features/Benefits
- Social proof
- CTA section

JSON formatında yanıt ver."""
        
        response = self.ai.complete_json(prompt, max_tokens=3000)
        import json
        return json.loads(response)
