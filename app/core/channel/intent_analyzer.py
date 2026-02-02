"""
AI ile niyet analizi yapar.
Her kanal için uygun niyet tiplerini filtreler.
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
import json
import time
import random

from app.database.models import (
    ChannelCandidate, IntentAnalysis, Keyword, ScoringRun
)
from app.core.constants import CHANNEL_ACCEPTED_INTENTS, INTENT_TYPES
from app.generators.ai_service import AIService


class IntentAnalyzer:
    """
    AI destekli niyet analizcisi.
    Anahtar kelimelerin kullanıcı niyetini analiz eder.
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
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
        candidates = (
            self.db.query(ChannelCandidate, Keyword)
            .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )
        
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
            self.db.add(intent_analysis)
        
        self.db.commit()
        
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
        Maliyet optimizasyonu ve rate limit yönetimi için dinamik batchleme yapar.
        
        Args:
            keywords: Analiz edilecek kelimeler
            batch_size: Başlangıç batch boyutu
            channel: Kanal adı (fallback intent tipi için)
        
        Returns:
            Analiz sonuçları
        """
        all_results = []
        
        # Fallback için kanal bazlı varsayılan niyet tipi
        fallback_intent = CHANNEL_ACCEPTED_INTENTS.get(channel, ['informational'])[0]

        # Dinamik ayarlar
        current_batch_size = batch_size
        current_sleep = 1.0
        min_batch_size = 1
        max_batch_size = 50
        max_retries = 3

        i = 0
        while i < len(keywords):
            # Batch'i belirle
            end_index = min(i + current_batch_size, len(keywords))
            batch = keywords[i:end_index]

            prompt = self._build_intent_prompt(batch)
            
            retry_count = 0
            success = False
            batch_results = []

            while retry_count <= max_retries:
                try:
                    start_time = time.time()
                    response = self.ai_service.complete_json(
                        prompt=prompt,
                        max_tokens=2000,
                        temperature=0.3
                    )
                    duration = time.time() - start_time

                    # JSON parse
                    content = response
                    # Markdown temizliği
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].strip()

                    print(f"DEBUG: Raw AI Response: {content[:200]}...")  # Log first 200 chars

                    batch_data = json.loads(content)

                    # Eğer sonuç dict ise ve içinde liste varsa
                    if isinstance(batch_data, dict):
                        batch_results = batch_data.get('results', batch_data.get('keywords', [batch_data]))
                    else:
                        batch_results = batch_data

                    print(f"DEBUG: Parsed {len(batch_results)} results")

                    # Başarılı oldu
                    success = True

                    # Hızlı yanıt alındıysa batch boyutunu artır (rate limit yoksa)
                    if duration < 2.0 and current_batch_size < max_batch_size:
                        current_batch_size += 5

                    # Bekleme süresini yavaşça azalt (decay)
                    current_sleep = max(1.0, current_sleep * 0.8)

                    time.sleep(current_sleep)
                    break

                except Exception as e:
                    error_str = str(e).lower()
                    is_rate_limit = "429" in error_str or "resource" in error_str or "exhausted" in error_str or "quota" in error_str

                    if is_rate_limit:
                        retry_count += 1
                        if retry_count > max_retries:
                            print(f"ERROR: Max retries reached for batch starting at {i}. Error: {e}")
                            break

                        # Backoff stratejisi
                        wait_time = (2 ** retry_count) + random.uniform(0, 1)
                        print(f"WARNING: Rate limit hit. Waiting {wait_time:.2f}s. Reducing batch size.")
                        time.sleep(wait_time)

                        # Batch boyutunu azalt
                        current_batch_size = max(min_batch_size, current_batch_size // 2)

                        # Gelecek çağrılar için base sleep süresini artır
                        current_sleep = min(10.0, current_sleep * 2)

                        # Not: Döngü başa dönecek ve aynı batch (prompt) tekrar denenecek.
                        # Eğer çok büyükse yine hata verebilir, ama current_batch_size sonraki iterasyonlarda küçülecek.

                    else:
                        print(f"ERROR: Intent Analysis Failed: {e}")
                        break

            if success:
                all_results.extend(batch_results)
            else:
                # Fallback: Bu batch için varsayılan değerler
                print(f"WARNING: Fallback used for {len(batch)} keywords due to errors.")
                for kw in batch:
                    all_results.append({
                        'keyword_id': kw['id'],
                        'intent_type': fallback_intent,
                        'confidence': 0.5,
                        'reasoning': 'AI çağrısı başarısız, varsayılan değer kullanıldı.'
                    })

            # Sonraki gruba geç
            i += len(batch)
        
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
