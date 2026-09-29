"""
GEO Compliance Checker - 7 Kriter.
Roadmap2.md - Bölüm 6

AI destekli kontrol yapar. GEO kriterleri anlam ve kalite odaklıdır,
bu nedenle AI değerlendirmesi gerekir.
"""
from typing import Dict, Any, List, Optional
from decimal import Decimal
import json
import re

from app.generators.ai_service import AIService, scoped
from app.generators.seo_geo.prompt_templates import GEO_COMPLIANCE_CHECK_PROMPT
from app.core.constants import GEO_COMPLIANCE_CRITERIA_V2


class GEOComplianceChecker:
    """
    AI destekli GEO uyumluluk kontrolü.
    7 anlam ve kalite odaklı kriteri AI ile değerlendirir.
    """
    
    CRITERIA = [
        "intro_answers_question",
        "snippet_extractable",
        "info_hierarchy_strong",
        "tone_is_informative",
        "no_fluff_content",
        "direct_answer_present",
        "has_verifiable_info"
    ]
    
    def __init__(self, ai_service: AIService):
        """
        Args:
            ai_service: AI servis instance (Gemini, OpenAI, vb.)
        """
        self.ai_service = scoped(ai_service, "geo_compliance")
    
    def check(
        self,
        content: Dict[str, Any],
        keyword: str
    ) -> Dict[str, Any]:
        """
        7 GEO kriterini AI ile kontrol eder.
        
        Args:
            content: İçerik verisi (ContentStructure formatında)
            keyword: Hedef anahtar kelime
            
        Returns:
            GEO uyumluluk raporu
        """
        # İçeriği string'e çevir
        full_content = self._content_to_string(content)
        
        # AI'a gönder. İlk 60 / 200 kelime deterministik KESİLİP ayrıca
        # verilir (checklist P100: 40-60 kelimede cevap, ~200 kelimede özet);
        # "cevap/özet var mı" yargısı AI'da kalır — ek çağrı yok.
        prompt = GEO_COMPLIANCE_CHECK_PROMPT.format(
            keyword=keyword,
            content=full_content,
            first_60_words=self._first_words(content, 60),
            first_200_words=self._first_words(content, 200),
        )
        
        try:
            response = self.ai_service.complete_json(prompt, max_tokens=6000)
            # AI yanıtı markdown code fence içinde gelebilir; temizle
            if '```' in response:
                response = re.sub(r'```(?:json)?\s*', '', response).strip()
            result = json.loads(response)

            return self._parse_ai_response(result)
        except Exception as e:
            # AI hatası durumunda fallback
            return self._fallback_check(content, keyword, str(e))
    
    @staticmethod
    def _first_words(content: Dict[str, Any], limit: int) -> str:
        """Giriş + gövde bölümlerinin (okuma sırasıyla) ilk `limit` kelimesi."""
        parts = [content.get('intro_paragraph', '') or '']
        parts.extend(
            s if isinstance(s, str) else str(s)
            for s in content.get('body_sections', []) or []
        )
        return ' '.join(' '.join(parts).split()[:limit])

    def _content_to_string(self, content: Dict[str, Any]) -> str:
        """İçeriği AI için string formatına çevirir."""
        parts = []
        
        # Başlık
        if 'title' in content:
            parts.append(f"# {content['title']}")
        
        # Giriş paragrafı
        if 'intro_paragraph' in content:
            parts.append(f"\n{content['intro_paragraph']}")
        
        # Alt başlıklar ve bölümler
        subheadings = content.get('subheadings', [])
        body_sections = content.get('body_sections', [])
        
        for i, (heading, section) in enumerate(zip(subheadings, body_sections)):
            parts.append(f"\n## {heading}")
            parts.append(section if isinstance(section, str) else str(section))
        
        # Bullet points
        bullet_points = content.get('bullet_points', [])
        if bullet_points:
            parts.append("\n**Önemli Noktalar:**")
            for bp in bullet_points:
                text = bp.get('text', bp) if isinstance(bp, dict) else bp
                parts.append(f"• {text}")
        
        return '\n'.join(parts)
    
    def _parse_ai_response(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """AI yanıtını parse eder ve standart formata çevirir."""
        checks = []
        
        # 7 kriter için check item oluştur
        detailed = result.get('detailed_analysis', [])
        detailed_map = {d['criterion']: d for d in detailed} if detailed else {}
        
        criteria_results = {}
        total_passed = 0
        
        for criterion in self.CRITERIA:
            passed = result.get(criterion, False)
            criteria_results[criterion] = passed
            
            if passed:
                total_passed += 1
            
            # Detaylı analiz varsa kullan
            detail = detailed_map.get(criterion, {})
            reasoning = detail.get('reasoning', '')
            
            checks.append({
                'criterion': criterion,
                'passed': passed,
                'reasoning': reasoning
            })
        
        score = total_passed / len(self.CRITERIA)
        
        # İyileştirme notları
        improvement_notes = result.get('improvement_notes', '')
        if not improvement_notes:
            improvement_notes = self._generate_improvement_notes(checks)
        
        return {
            **criteria_results,
            'checks': checks,
            'total_passed': total_passed,
            'total_checks': len(self.CRITERIA),
            'score': round(score, 2),
            'ai_snippet_preview': result.get('ai_snippet_preview', ''),
            'improvement_notes': improvement_notes,
            'evaluation_source': 'ai',
        }
    
    def _fallback_check(
        self,
        content: Dict[str, Any],
        keyword: str,
        error: str
    ) -> Dict[str, Any]:
        """AI hatası durumunda sonuç: kriterler DEĞERLENDİRİLMEDİ.

        GEO kriterleri öznel kalite yargısıdır; regex/heuristikle "geçti"
        işaretlenmez. Tüm kriterler False + evaluation_source='fallback'
        döner: generator bu sonucu revizyon hedefi yapmaz, yayın öncesi
        inceleme özeti manuel kontrol ister. (DB'de ayırt etmek için not
        GEO_FALLBACK_NOTE_PREFIX ile başlar.)
        """
        from app.compliance.publish_review import GEO_FALLBACK_NOTE_PREFIX

        reasoning = 'Değerlendirilemedi (AI kontrolü başarısız) — manuel inceleme gerekli'
        checks = [
            {'criterion': c, 'passed': False, 'reasoning': reasoning}
            for c in self.CRITERIA
        ]
        intro = content.get('intro_paragraph', '')
        return {
            **{c: False for c in self.CRITERIA},
            'checks': checks,
            'total_passed': 0,
            'total_checks': len(self.CRITERIA),
            'score': 0.0,
            'ai_snippet_preview': intro[:200] if intro else '',
            'improvement_notes': (
                f'{GEO_FALLBACK_NOTE_PREFIX} ({error}); GEO kriterleri '
                f'değerlendirilmedi, yayın öncesi manuel inceleme gerekli.'
            ),
            'evaluation_source': 'fallback',
        }

    def _generate_improvement_notes(self, checks: List[Dict[str, Any]]) -> str:
        """İyileştirme notları üretir."""
        failed = [c for c in checks if not c['passed']]
        
        if not failed:
            return "GEO uyumluluğu mükemmel durumda."
        
        criterion_descriptions = {
            'intro_answers_question': 'Giriş paragrafı soruya doğrudan yanıt vermeli',
            'snippet_extractable': 'Giriş paragrafı bağımsız anlamlı olmalı',
            'info_hierarchy_strong': 'Özet→Detay→Örnek yapısı güçlendirilmeli',
            'tone_is_informative': 'Ton daha bilgilendirici olmalı',
            'no_fluff_content': 'Gereksiz dolgu cümleler kaldırılmalı',
            'direct_answer_present': 'İlk 50 kelimede cevap verilmeli',
            'has_verifiable_info': 'Somut veri veya kaynak eklenmeli'
        }
        
        notes = []
        for check in failed[:3]:  # En fazla 3 öneri
            criterion = check['criterion']
            if criterion in criterion_descriptions:
                notes.append(criterion_descriptions[criterion])
        
        return '. '.join(notes) + '.' if notes else ''


# Backward compatibility için eski sınıf adı
GeoChecker = GEOComplianceChecker
