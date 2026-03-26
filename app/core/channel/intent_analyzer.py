"""
AI ile niyet analizi yapar.
Her kanal için uygun niyet tiplerini filtreler.
"""
from typing import List, Dict, Any, Optional
import logging
from sqlalchemy.orm import Session
import json
import time
import random
import re

from app.database.models import (
    ChannelCandidate, IntentAnalysis, Keyword, ScoringRun
)
from app.core.constants import (
    CHANNEL_ACCEPTED_INTENTS, INTENT_TYPES,
    ADS_HARD_NEGATIVE_TERMS, SOCIAL_HARD_NEGATIVE_TERMS,
    INTENT_FALLBACK_CONFIDENCE,
)
from app.generators.ai_service import AIService


logger = logging.getLogger(__name__)


class IntentAnalyzer:
    """
    AI destekli niyet analizcisi.
    Anahtar kelimelerin kullanıcı niyetini analiz eder.
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service

    def _parse_intent_json(self, raw: str) -> List[Dict[str, Any]]:
        """Best-effort JSON parse for intent responses."""
        if not raw or not raw.strip():
            raise ValueError("AI boş yanıt döndürdü")

        text = raw.strip()

        # Markdown fence cleanup
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Normalize common issues
        text = text.replace("\ufeff", "")
        text = text.replace("“", '"').replace("”", '"')
        text = text.replace("’", "'").replace("‘", "'")
        text = re.sub(r",\s*([}\]])", r"\1", text)

        candidates = [text]
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        ends = [i for i in (text.rfind("]"), text.rfind("}")) if i >= 0]
        if starts:
            candidates.append(text[min(starts):])
        if ends:
            candidates.append(text[:max(ends) + 1])
        if starts and ends and max(ends) >= min(starts):
            candidates.append(text[min(starts):max(ends) + 1])

        uniq = []
        seen = set()
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                uniq.append(c)

        parse_errors = []
        for cand in uniq:
            for attempt in (cand, self._close_unbalanced_json(cand)):
                try:
                    data = json.loads(attempt)
                    if isinstance(data, dict):
                        data = data.get('results', data.get('keywords', [data]))
                    if isinstance(data, list):
                        return data
                except Exception as e:
                    parse_errors.append(str(e))
                    continue

        extracted = self._extract_object_list(text)
        if extracted:
            return extracted

        raise ValueError(
            f"Intent JSON parse failed: {parse_errors[-1] if parse_errors else 'unknown error'}"
        )

    def _close_unbalanced_json(self, text: str) -> str:
        s = text.strip()
        if not s:
            return s
        if s.count("[") > s.count("]"):
            s += "]" * (s.count("[") - s.count("]"))
        if s.count("{") > s.count("}"):
            s += "}" * (s.count("{") - s.count("}"))
        return s

    def _extract_object_list(self, text: str) -> List[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        out = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] != "{":
                i += 1
                continue
            try:
                obj, end = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    out.append(obj)
                i += end
            except Exception:
                i += 1
        return out
    
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
        fallback_intent = CHANNEL_ACCEPTED_INTENTS.get(channel, ['informational'])[0]
        
        # keyword_id bazlı lookup map oluştur (zip yerine — sıra kayması riski yok)
        # AI keyword_id'yi string döndürebilir → int'e zorla
        intent_map = {}
        for result in intent_results:
            kid = result.get('keyword_id')
            if kid is not None:
                try:
                    intent_map[int(kid)] = result
                except (ValueError, TypeError):
                    pass
        
        for candidate, keyword in candidates:
            # keyword_id ile eşleştir (pozisyonel değil)
            intent_result = intent_map.get(candidate.keyword_id)
            
            if intent_result is None:
                # AI yanıtında bu keyword eksik — fallback
                intent_result = {
                    'intent_type': fallback_intent,
                    'confidence': 0.5,
                    'reasoning': 'AI yanıtında keyword_id bulunamadı, varsayılan değer'
                }
            
            # Tekil sonuç işleme
            processed_result = self._process_intent_result(
                intent_result=intent_result,
                accepted_intents=accepted_intents,
                channel=channel,
                keyword_text=keyword.keyword
            )
            
            if processed_result['is_passed']:
                passed_count += 1
            
            # Veritabanına kaydet
            intent_analysis = IntentAnalysis(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel=channel,
                intent_type=processed_result['intent_type'],
                confidence_score=processed_result['confidence'],
                ai_reasoning=processed_result['reasoning'],
                is_passed=processed_result['is_passed']
            )
            self.db.add(intent_analysis)
        
        self.db.commit()
        
        return {
            'channel': channel,
            'analyzed': len(candidates),
            'passed': passed_count,
            'filtered_out': len(candidates) - passed_count
        }
    
    def _process_intent_result(
        self,
        intent_result: Dict[str, Any],
        accepted_intents: List[str],
        channel: str,
        keyword_text: str
    ) -> Dict[str, Any]:
        """
        Tekil niyet analiz sonucunu işler ve kurallara göre değerlendirir.

        Args:
            intent_result: AI'dan gelen ham sonuç
            accepted_intents: Kanal için kabul edilen niyet tipleri

        Returns:
            İşlenmiş sonuç ve geçme durumu
        """
        intent_type = intent_result.get('intent_type', 'informational')

        # Güven skoru kontrolü ve dönüşümü
        confidence_raw = intent_result.get('confidence', 0.5)
        try:
            confidence = float(confidence_raw)
        except (ValueError, TypeError):
            confidence = 0.5

        reasoning = intent_result.get('reasoning', '')

        # Filtre kuralları (hafif sıkılaştırma):
        # 1. Niyet tipi kabul edilenler listesinde olmalı
        # 2. Kanal bazlı minimum güven eşiğini sağlamalı
        is_intent_accepted = intent_type in accepted_intents
        min_confidence_by_channel = {
            "ADS": 0.52,
            "SEO": 0.45,
            "SOCIAL": 0.50,
        }
        min_confidence = min_confidence_by_channel.get(channel, 0.45)
        is_confidence_sufficient = confidence >= min_confidence

        is_passed = is_intent_accepted and is_confidence_sufficient

        # ADS için hard-negative sinyaller: niyet ne olursa olsun geçmesin.
        kw_norm = (keyword_text or "").lower()
        if channel == "ADS" and any(term in kw_norm for term in ADS_HARD_NEGATIVE_TERMS):
            is_passed = False
            if reasoning:
                reasoning = f"{reasoning} | ADS hard-negative sinyal tespit edildi"
            else:
                reasoning = "ADS hard-negative sinyal tespit edildi"

        # SOCIAL için ilan/ikinci el/junk sorguları engelle.
        if channel == "SOCIAL" and any(term in kw_norm for term in SOCIAL_HARD_NEGATIVE_TERMS):
            is_passed = False
            if reasoning:
                reasoning = f"{reasoning} | SOCIAL hard-negative sinyal tespit edildi"
            else:
                reasoning = "SOCIAL hard-negative sinyal tespit edildi"

        return {
            'intent_type': intent_type,
            'confidence': confidence,
            'reasoning': reasoning,
            'is_passed': is_passed
        }

    def _batch_analyze_intent(
        self,
        keywords: List[Dict[str, Any]],
        batch_size: int = 8,
        channel: str = 'SEO'
    ) -> List[Dict[str, Any]]:
        """
        Kelimeleri batch halinde AI'a gönderir.
        Maliyet optimizasyonu için toplu işlem yapar.
        Retry + backoff + jitter ile rate-limit korumalı.
        """
        INTENT_MAX_RETRIES = 3
        INTENT_BASE_DELAY = 2  # seconds
        RETRIABLE_PATTERNS = ["429", "503", "overloaded", "Resource exhausted", "rate_limit"]
        
        all_results = []
        
        # Fallback için kanal bazlı varsayılan niyet tipi
        fallback_intent = CHANNEL_ACCEPTED_INTENTS.get(channel, ['informational'])[0]
        fallback_confidence = INTENT_FALLBACK_CONFIDENCE.get(channel, 0.43)
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]
            prompt = self._build_intent_prompt(batch, channel=channel)
            success = False
            
            for attempt in range(INTENT_MAX_RETRIES):
                try:
                    response = self.ai_service.complete_json(
                        prompt=prompt,
                        max_tokens=2000,
                        temperature=0.3
                    )
                    
                    logger.debug(f"Raw AI Response: {str(response)[:200]}...")
                    batch_results = self._parse_intent_json(response)
                    
                    logger.debug(f"Parsed {len(batch_results)} results")
                    all_results.extend(batch_results)
                    
                    # Partial parse: batch uzunluğu uyuşmazsa eksik keyword'lere fallback
                    # AI keyword_id'yi string döndürebilir → int'e zorla
                    parsed_ids = set()
                    for r in batch_results:
                        if isinstance(r, dict) and r.get('keyword_id') is not None:
                            try:
                                parsed_ids.add(int(r['keyword_id']))
                            except (ValueError, TypeError):
                                pass
                    for kw in batch:
                        if kw['id'] not in parsed_ids:
                            all_results.append({
                                'keyword_id': kw['id'],
                                'intent_type': fallback_intent,
                                'confidence': fallback_confidence,
                                'reasoning': 'AI yanıtında bu keyword eksik, varsayılan değer'
                            })
                    
                    success = True
                    break
                    
                except Exception as e:
                    error_str = str(e)
                    is_retriable = any(p in error_str for p in RETRIABLE_PATTERNS)
                    
                    if is_retriable and attempt < INTENT_MAX_RETRIES - 1:
                        delay = INTENT_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            f"Intent rate-limit, retry {attempt+1}/{INTENT_MAX_RETRIES} "
                            f"in {delay:.1f}s: {error_str[:100]}"
                        )
                        time.sleep(delay)
                        continue
                    
                    # Non-retriable veya son deneme — fallback
                    logger.error(
                        f"Intent Analysis Failed (attempt {attempt+1}/{INTENT_MAX_RETRIES}): {e}"
                    )
                    logger.error(
                        f"Content that failed: {response if 'response' in dir() else 'No response received'}"
                    )
                    for kw in batch:
                        all_results.append({
                            'keyword_id': kw['id'],
                            'intent_type': fallback_intent,
                            'confidence': fallback_confidence,
                            'reasoning': f'AI çağrısı başarısız ({attempt+1} deneme): {str(e)}'
                        })
                    break
        
        return all_results
    
    def _build_intent_prompt(
        self,
        keywords: List[Dict[str, Any]],
        channel: str = "SEO"
    ) -> str:
        """Niyet analizi için prompt oluşturur."""
        keyword_list = "\n".join([f"- {kw['id']}: {kw['keyword']}" for kw in keywords])
        
        channel_hint = ""
        if channel == "SOCIAL":
            channel_hint = """
KANAL BAĞLAMI (SOCIAL):
- Satın alma odaklı olsa bile, eğer keyword üzerinden karşılaştırma, yanlış bilinenler,
  fiyat/performans tartışması, "en iyi X" listesi veya güçlü bir kısa video kancası üretilebiliyorsa
  "commercial" veya "trend_worthy" seçebilirsin.
- Sadece ürün adı geçiyor diye otomatik transactional verme.
- İçerikleştirilebilir/tartışma yaratabilir ürün terimlerinde commercial önceliği kullan."""
        elif channel == "ADS":
            channel_hint = """
KANAL BAĞLAMI (ADS):
- Dönüşüm odaklı sorgularda transactional/commercial önceliklendir.
- Bilgi amaçlı ve genel öğrenme sorgularında informational kullan."""
        elif channel == "SEO":
            channel_hint = """
KANAL BAĞLAMI (SEO):
- Rehber, nasıl yapılır, nedir, karşılaştırma ve açıklama odaklı sorgularda informational/commercial kullan.
- Satın alma niyeti netse transactional kullan."""

        prompt = f"""Aşağıdaki anahtar kelimelerin kullanıcı niyetini analiz et.

Her kelime için şu niyet tiplerinden birini belirle:
- transactional: Satın alma niyeti (ör: "laptop satın al", "en ucuz telefon")
- informational: Bilgi arayışı (ör: "python nedir", "grip belirtileri")
- navigational: Marka/site yönelimi (ör: "facebook giriş", "amazon")
- commercial: Araştırma + potansiyel satın alma (ör: "en iyi laptop 2024", "iphone vs samsung")
- trend_worthy: Viral/trend potansiyeli (ör: "yeni tiktok trendi", "viral challenge")
{channel_hint}

Anahtar Kelimeler:
{keyword_list}

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:

[
  {{"keyword_id": 1, "intent_type": "transactional", "confidence": 0.9, "reasoning": "..."}},
  ...
]

ZORUNLU:
- Girdideki her keyword_id çıktı listesinde tam 1 kez olmalı.
- keyword_id integer dönmeli.
"""
        return prompt
