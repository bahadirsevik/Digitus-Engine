"""
AI ile niyet analizi yapar.
Her kanal için uygun niyet tiplerini filtreler.
"""
from typing import List, Dict, Any, Optional
import json
import logging

from app.database.models import IntentAnalysis
from app.core.constants import CHANNEL_ACCEPTED_INTENTS, INTENT_TYPES
from app.generators.ai_service import AIService
from app.repositories.intent_repository import IntentRepository

logger = logging.getLogger(__name__)

class IntentAnalyzer:
    """
    AI destekli niyet analizcisi.
    Anahtar kelimelerin kullanıcı niyetini analiz eder.
    """
    
    def __init__(self, intent_repository: IntentRepository, ai_service: AIService):
        self.repo = intent_repository
        self.ai_service = ai_service
    
    def analyze_candidates(self, scoring_run_id: int, channel: str) -> Dict[str, Any]:
        """
        Belirli bir kanal için tüm adayların niyet analizini yapar.
        
        Args:
            scoring_run_id: Skorlama çalıştırma ID'si
            channel: Kanal adı ('ADS', 'SEO', 'SOCIAL')
        
        Returns:
            Analiz özeti
        """
        # Adayları al
        candidates = self.repo.get_candidates(scoring_run_id, channel)
        
        if not candidates:
            return {'analyzed': 0, 'passed': 0}
        
        # Kabul edilen niyet tipleri
        accepted_intents = CHANNEL_ACCEPTED_INTENTS.get(channel, [])
        
        # Toplu analiz için kelimeleri hazırla
        keywords_to_analyze = [
            {'id': candidate.keyword_id, 'keyword': keyword.keyword}
            for candidate, keyword in candidates
        ]
        
        # AI ile toplu niyet analizi (channel-aware fallback için)
        intent_results = self._batch_analyze_intent(keywords_to_analyze, channel=channel)
        
        passed_count = 0
        intent_analyses = []
        
        for (candidate, keyword), intent_result in zip(candidates, intent_results):
            intent_type = intent_result.get('intent_type', 'informational')
            confidence = intent_result.get('confidence', 0.5)
            reasoning = intent_result.get('reasoning', '')
            
            # Filtreyi geçti mi?
            is_passed = intent_type in accepted_intents
            
            if is_passed:
                passed_count += 1
            
            # Veritabanına kaydet
            intent_analysis = IntentAnalysis(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel=channel,
                intent_type=intent_type,
                confidence_score=confidence,
                ai_reasoning=reasoning,
                is_passed=is_passed
            )
            intent_analyses.append(intent_analysis)
        
        self.repo.save_intent_analyses(intent_analyses)
        
        return {
            'channel': channel,
            'analyzed': len(candidates),
            'passed': passed_count,
            'filtered_out': len(candidates) - passed_count
        }
    
    def _batch_analyze_intent(
        self,
        keywords: List[Dict[str, Any]],
        batch_size: int = 20,
        channel: str = 'SEO'
    ) -> List[Dict[str, Any]]:
        """
        Kelimeleri batch halinde AI'a gönderir.
        Maliyet optimizasyonu için toplu işlem yapar.
        
        Args:
            keywords: Analiz edilecek kelimeler
            batch_size: Her batch'teki kelime sayısı
            channel: Kanal adı (fallback intent tipi için)
        
        Returns:
            Analiz sonuçları
        """
        all_results = []
        
        # Fallback için kanal bazlı varsayılan niyet tipi
        fallback_intent = CHANNEL_ACCEPTED_INTENTS.get(channel, ['informational'])[0]        
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]
            prompt = self._build_intent_prompt(batch)
            response = None  # Initialize before try block
            
            try:
                response = self.ai_service.complete_json(
                    prompt=prompt,
                    max_tokens=2000,
                    temperature=0.3
                )
                
                # JSON parse
                content = response
                # Markdown temizliği
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].strip()
                
                logger.debug(f"DEBUG: Raw AI Response: {content[:200]}...")  # Log first 200 chars
                
                batch_results = json.loads(content)
                
                # Eğer sonuç dict ise ve içinde liste varsa
                if isinstance(batch_results, dict):
                    batch_results = batch_results.get('results', batch_results.get('keywords', [batch_results]))
                
                logger.debug(f"DEBUG: Parsed {len(batch_results)} results")
                all_results.extend(batch_results)
                
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"ERROR: Intent Analysis Failed: {e}")
                logger.error(f"ERROR: Content that failed: {response if response else 'No response received'}")
                # Fallback: her kelime için kanal bazlı varsayılan değer
                for kw in batch:
                    all_results.append({
                        'keyword_id': kw['id'],
                        'intent_type': fallback_intent,
                        'confidence': 0.5,
                        'reasoning': f'AI çağrısı başarısız, varsayılan değer kullanıldı: {str(e)}'
                    })
        
        return all_results
    
    def _build_intent_prompt(self, keywords: List[Dict[str, Any]]) -> str:
        """Niyet analizi için prompt oluşturur."""
        keyword_list = "\n".join([f"- {kw['id']}: {kw['keyword']}" for kw in keywords])
        
        prompt = f"""Aşağıdaki anahtar kelimelerin kullanıcı niyetini analiz et.

Her kelime için şu niyet tiplerinden birini belirle:
- transactional: Satın alma niyeti (ör: "laptop satın al", "en ucuz telefon")
- informational: Bilgi arayışı (ör: "python nedir", "grip belirtileri")
- navigational: Marka/site yönelimi (ör: "facebook giriş", "amazon")
- commercial: Araştırma + potansiyel satın alma (ör: "en iyi laptop 2024", "iphone vs samsung")
- trend_worthy: Viral/trend potansiyeli (ör: "yeni tiktok trendi", "viral challenge")

Anahtar Kelimeler:
{keyword_list}

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:

[
  {{"keyword_id": 1, "intent_type": "transactional", "confidence": 0.9, "reasoning": "..."}},
  ...
]
"""
        return prompt
