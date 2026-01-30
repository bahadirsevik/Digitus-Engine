"""
Social media content generator.
"""
from typing import Dict, Any, List

from app.generators.ai_service import AIService


class SocialGenerator:
    """Sosyal medya içerik üretici."""
    
    # Platform karakter limitleri
    PLATFORM_LIMITS = {
        'twitter': 280,
        'instagram': 2200,
        'linkedin': 3000,
        'facebook': 63206,
        'tiktok': 2200
    }
    
    def __init__(self, ai_service: AIService):
        self.ai = ai_service
    
    def generate_post(
        self,
        keyword: str,
        platform: str,
        tone: str = "professional"
    ) -> Dict[str, Any]:
        """
        Platform için optimize edilmiş post üretir.
        
        Returns:
            - caption: Paylaşım metni
            - hashtags: Hashtag listesi
            - cta: Call-to-action
            - media_suggestion: Görsel/video önerisi
        """
        limit = self.PLATFORM_LIMITS.get(platform.lower(), 2000)
        
        prompt = f"""Sosyal medya paylaşımı oluştur:

Platform: {platform}
Karakter Limiti: {limit}
Anahtar Kelime/Konu: {keyword}
Ton: {tone}

Kurallar:
- Dikkat çekici açılış
- Emoji kullanımı (uygunsa)
- Net CTA
- İlgili hashtag'ler

JSON formatında yanıt ver:
{{
  "caption": "...",
  "hashtags": ["#...", ...],
  "cta": "...",
  "media_suggestion": "...",
  "best_posting_times": ["...", ...]
}}"""
        
        response = self.ai.complete_json(prompt)
        import json
        return json.loads(response)
    
    def generate_content_calendar(
        self,
        keywords: List[str],
        platforms: List[str],
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Haftalık içerik takvimi oluşturur.
        """
        kw_list = ", ".join(keywords[:5])
        platform_list = ", ".join(platforms)
        
        prompt = f"""{days} günlük sosyal medya içerik takvimi oluştur:

Ana Konular: {kw_list}
Platformlar: {platform_list}

Her gün için:
- Platform
- Paylaşım zamanı
- İçerik tipi (image/video/carousel/story)
- Kısa içerik özeti

JSON formatında yanıt ver."""
        
        response = self.ai.complete_json(prompt, max_tokens=2000)
        import json
        return json.loads(response)
