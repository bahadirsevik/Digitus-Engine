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
    INTENT_FALLBACK_CONFIDENCE, INTENT_MIN_CONFIDENCE,
    INTENT_MAX_TOKENS, INTENT_BATCH_SIZE, INTENT_MISSING_MAX_RETRIES,
    SINGLE_RETRY_MAX_PER_STAGE,
    INTENT_SOURCE_AI, INTENT_SOURCE_FALLBACK,
)
from app.core.channel.ai_budget import AiBudgetExhausted, AiCallBudget
from app.core.telemetry.ai_cost_budget import BudgetError
from app.core.channel.brand_defense import (
    load_brand_defense_context, is_own_brand_keyword,
    load_product_definition,
)


def _coerce_grade(value) -> Optional[bool]:
    """AI'dan gelen G_T/G_A değerini bool'a zorlar; parse edilemezse None.

    Gemini JSON drift toleransı: 0/1 (int), "0"/"1" (string sayı),
    true/false (bool) ve "true"/"yes"/"false"/"no" string varyantları kabul.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "yes", "evet"):
            return True
        if normalized in ("false", "no", "hayir", "hayır"):
            return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None
from app.generators.ai_service import (
    AIService,
    is_transport_error,
    logical_request,
    mark_attempt_failed,
    scoped,
)


logger = logging.getLogger(__name__)


class IntentAnalyzer:
    """
    AI destekli niyet analizcisi.
    Anahtar kelimelerin kullanıcı niyetini analiz eder.
    """

    # Structured output şemaları (brand_filter kalıbı). SEO'da gt/ga zorunlu.
    _ITEM_BASE_PROPS = {
        "keyword_id": {"type": "integer"},
        "intent_type": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    }
    RESPONSE_SCHEMA_BASE = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": dict(_ITEM_BASE_PROPS),
                    "required": ["keyword_id", "intent_type", "confidence"],
                },
            }
        },
        "required": ["results"],
    }
    RESPONSE_SCHEMA_SEO = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **_ITEM_BASE_PROPS,
                        "gt": {"type": "integer"},
                        "ga": {"type": "integer"},
                    },
                    "required": ["keyword_id", "intent_type", "confidence", "gt", "ga"],
                },
            }
        },
        "required": ["results"],
    }
    # v2.1 (Faz D): SEO'da gt yerine strategy_fit (S_G — içerik stratejisi
    # beyanına uyum). gt kolonu v2_1 satırlarında NULL kalır (denetim kaydı).
    RESPONSE_SCHEMA_SEO_V21 = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **_ITEM_BASE_PROPS,
                        "strategy_fit": {"type": "integer"},
                        "ga": {"type": "integer"},
                    },
                    "required": [
                        "keyword_id", "intent_type", "confidence",
                        "strategy_fit", "ga",
                    ],
                },
            }
        },
        "required": ["results"],
    }

    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = scoped(ai_service, "intent")
        # analyze_candidates çağrısı başına sıfırlanan durum
        self._ai_calls_used = 0
        self._single_retries_used = 0
        self._budget: Optional[AiCallBudget] = None
        # v2.1 (Faz D): analyze_candidates çağrısı başına set edilen bağlam —
        # tüm retry/prompt/parse yolları otomatik aynı sürümü kullanır
        self._algorithm_version: str = "v2"
        self._strategy_declaration: Optional[str] = None

    def _complete_json(self, prompt: str, channel: str) -> str:
        """Tek AI çağrısı — HARD bütçe kontrolü + gerçek çağrı sayacı."""
        if self._budget is not None and not self._budget.try_consume():
            raise AiBudgetExhausted("Intent: expansion AI bütçesi tükendi")
        self._ai_calls_used += 1
        if channel == "SEO":
            schema = (
                self.RESPONSE_SCHEMA_SEO_V21
                if self._algorithm_version == "v2_1"
                else self.RESPONSE_SCHEMA_SEO
            )
        else:
            schema = self.RESPONSE_SCHEMA_BASE
        return self.ai_service.complete_json(
            prompt=prompt,
            max_tokens=INTENT_MAX_TOKENS,
            temperature=0.3,
            response_schema=schema,
        )

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
    
    def analyze_candidates(
        self,
        scoring_run_id: int,
        channel: str,
        keyword_ids: Optional[List[int]] = None,
        progress_callback=None,
        source: str = INTENT_SOURCE_AI,
        budget: Optional[AiCallBudget] = None,
    ) -> Dict[str, Any]:
        """
        Belirli bir kanal için tüm adayların niyet analizini yapar.

        Args:
            scoring_run_id: Skorlama çalıştırma ID'si
            channel: Kanal adı ('ADS', 'SEO', 'SOCIAL')
            source: Başarılı AI sonuçlarına yazılacak kaynak etiketi
                    (transfer akışı 'transfer_ai' geçer); fallback'ler her
                    zaman 'fallback' ile işaretlenir.
            budget: Expansion HARD AI bütçesi (None = sınırsız)

        Returns:
            Analiz özeti (ai_calls_used dahil)
        """
        self._ai_calls_used = 0
        self._single_retries_used = 0
        self._budget = budget
        # Adayları al
        # Terminal hard-block satırları (COMPETITOR_TERM vb.) intent'e girmez —
        # rakip aday için AI çağrısı SIFIRDIR (plan A)
        from sqlalchemy import and_ as _and
        from app.core.constants import TERMINAL_PREFILTER_REASONS
        from app.database.models import PreFilterResult as _PFR

        candidates_query = (
            self.db.query(ChannelCandidate, Keyword)
            .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .outerjoin(
                _PFR,
                _and(
                    _PFR.keyword_id == ChannelCandidate.keyword_id,
                    _PFR.scoring_run_id == ChannelCandidate.scoring_run_id,
                    _PFR.channel == ChannelCandidate.channel,
                    _PFR.extra_data["reason_code"].as_string().in_(
                        TERMINAL_PREFILTER_REASONS
                    ),
                ),
            )
            .filter(_PFR.id.is_(None))
        )
        if keyword_ids:
            candidates_query = candidates_query.filter(ChannelCandidate.keyword_id.in_(keyword_ids))
        candidates = candidates_query.order_by(ChannelCandidate.rank_in_channel).all()
        
        if not candidates:
            # Erken dönüş de ai_calls_used sözleşmesine uyar (plan A1)
            return {'analyzed': 0, 'passed': 0, 'ai_calls_used': 0}
        
        # Kabul edilen niyet tipleri
        accepted_intents = CHANNEL_ACCEPTED_INTENTS.get(channel, [])

        # Marka savunma baglami (yalnizca confirmed profil; yoksa None)
        run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        brand_ctx = load_brand_defense_context(self.db, run)

        # v2.1 (Faz D): cagri-basina baglam — SEO'da gt yerine strategy_fit
        # (S_G) sorulur, ADS'te genel intent ELEME OTORITESINI kaybeder.
        # Codex 3. tur #1 (FAIL-CLOSED): v2_1'de baglam YALNIZ manifest
        # snapshot'indan gelir; canli load_product_definition HIC cagrilmaz,
        # snapshot yok/eksikse AI cagrisindan ONCE tipli hata.
        self._algorithm_version = (
            getattr(run, "algorithm_version", "v2") or "v2" if run else "v2"
        )
        self._strategy_declaration = None
        if self._algorithm_version == "v2_1":
            from app.core.policy.channel_strategy import (
                require_dispatch_strategy_context,
            )

            snapshot = require_dispatch_strategy_context(run)
            self._strategy_declaration = snapshot["content_strategy"]
            product_definition = (
                snapshot["product_definition"] if channel == 'SEO' else None
            )
        else:
            # SEO G_A derecesi icin musteri urun tanimi (confirmed profil yoksa None)
            product_definition = (
                load_product_definition(self.db, run) if channel == 'SEO' else None
            )

        # Toplu analiz için kelimeleri hazırla (rank: tekil retry önceliği)
        keywords_to_analyze = [
            {
                'id': candidate.keyword_id,
                'keyword': keyword.keyword,
                'rank': candidate.rank_in_channel,
            }
            for candidate, keyword in candidates
        ]

        # AI ile toplu niyet analizi (channel-aware fallback için)
        intent_results = self._batch_analyze_intent(
            keywords_to_analyze,
            channel=channel,
            brand_terms=list(brand_ctx.original_terms) if brand_ctx else None,
            product_definition=product_definition,
            progress_callback=progress_callback,
        )
        
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
                # AI yanıtında bu keyword eksik — fallback (retry'lar da tükendi)
                intent_result = {
                    'intent_type': fallback_intent,
                    'confidence': 0.5,
                    'reasoning': 'AI yanıtında keyword_id bulunamadı, varsayılan değer',
                    'is_fallback': True,
                }
            
            # Tekil sonuç işleme
            processed_result = self._process_intent_result(
                intent_result=intent_result,
                accepted_intents=accepted_intents,
                channel=channel,
                keyword_text=keyword.keyword,
                own_brand=is_own_brand_keyword(keyword.keyword, brand_ctx, channel),
            )
            
            if processed_result['is_passed']:
                passed_count += 1
            
            # Veritabanına kaydet
            existing = (
                self.db.query(IntentAnalysis)
                .filter(
                    IntentAnalysis.scoring_run_id == scoring_run_id,
                    IntentAnalysis.keyword_id == candidate.keyword_id,
                    IntentAnalysis.channel == channel,
                )
                .first()
            )
            # Source işaretleme: fallback her zaman görünür olur —
            # UPDATE ve INSERT dallarının İKİSİNDE de açıkça set edilir
            # (server_default'a güvenilmez; transfer_ai semantiği upsert'te
            # sessizce kaybolmasın).
            row_source = (
                INTENT_SOURCE_FALLBACK
                if intent_result.get('is_fallback')
                else source
            )

            if existing:
                existing.intent_type = processed_result['intent_type']
                existing.confidence_score = processed_result['confidence']
                existing.ai_reasoning = processed_result['reasoning']
                existing.is_passed = processed_result['is_passed']
                existing.gt = processed_result.get('gt')
                existing.ga = processed_result.get('ga')
                # v2.1 S_G — İKİ dalda da açıkça yazılır (NULL dahil;
                # sessizce 0 yapılmaz — plan Faz D sözleşmesi)
                existing.strategy_fit = processed_result.get('strategy_fit')
                existing.source = row_source
            else:
                intent_analysis = IntentAnalysis(
                    scoring_run_id=scoring_run_id,
                    keyword_id=candidate.keyword_id,
                    channel=channel,
                    intent_type=processed_result['intent_type'],
                    confidence_score=processed_result['confidence'],
                    ai_reasoning=processed_result['reasoning'],
                    is_passed=processed_result['is_passed'],
                    gt=processed_result.get('gt'),
                    ga=processed_result.get('ga'),
                    strategy_fit=processed_result.get('strategy_fit'),
                    source=row_source,
                )
                self.db.add(intent_analysis)
        
        self.db.commit()
        
        return {
            'channel': channel,
            'analyzed': len(candidates),
            'passed': passed_count,
            'filtered_out': len(candidates) - passed_count,
            # batches: gösterim metriği; bütçe muhasebesi ai_calls_used'a dayanır
            'batches': (len(candidates) + INTENT_BATCH_SIZE - 1) // INTENT_BATCH_SIZE,
            'ai_calls_used': self._ai_calls_used,
        }
    
    def _process_intent_result(
        self,
        intent_result: Dict[str, Any],
        accepted_intents: List[str],
        channel: str,
        keyword_text: str,
        own_brand: bool = False
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

        # SEO niyet dereceleri — v2: gt/ga; v2_1: strategy_fit(S_G)/ga
        # (gt v2_1 satırlarında NULL kalır — kolon anlamı DEĞİŞMEZ)
        is_v21 = self._algorithm_version == "v2_1"
        gt = (
            _coerce_grade(intent_result.get('gt'))
            if channel == 'SEO' and not is_v21 else None
        )
        ga = _coerce_grade(intent_result.get('ga')) if channel == 'SEO' else None
        strategy_fit = (
            _coerce_grade(intent_result.get('strategy_fit'))
            if channel == 'SEO' and is_v21 else None
        )

        # Filtre kuralları:
        # 1. Niyet tipi kabul edilenler listesinde olmalı
        # 2. Kanal bazlı minimum güven eşiğini sağlamalı
        is_intent_accepted = intent_type in accepted_intents
        min_confidence = INTENT_MIN_CONFIDENCE.get(channel, 0.45)
        is_confidence_sufficient = confidence >= min_confidence

        is_passed = is_intent_accepted and is_confidence_sufficient

        # v2.1 (Faz D / doc §4): ADS'te genel intent ELEME OTORİTESİ DEĞİLDİR —
        # tek AI eleme yetkisi ürün-bilgili prefilter sınıfıdır (-1).
        # intent_type/confidence audit olarak kaydedilir; deterministik
        # hard-negative/rakip/policy kontrolleri AŞAĞIDA aynen çalışır.
        if channel == 'ADS' and is_v21:
            is_passed = True

        # SEO INTENT ELEMEZ (Skorlama v2): tüm SEO adayları intent kapısından
        # geçer; niyet yalnızca G_T/G_A derecesi olarak önceliği artırır.
        # SEO'da eleme yetkisi yalnızca marka dışlama filtresi ve deterministik
        # price/fiyat filtresindedir (seo_prefilter).
        if channel == 'SEO':
            is_passed = True

        # Marka savunma bypass'i: kendi marka terimi intent/confidence
        # kapisina takilmaz. Hard-negative kontrolleri ASAGIDA yine calisir
        # ve gerekirse bu karari geri alir (brand defense onlari ezmez).
        if own_brand and not is_passed:
            is_passed = True
            suffix = "[brand_defense] Kendi marka terimi — intent kapisi bypass edildi"
            reasoning = f"{reasoning} | {suffix}" if reasoning else suffix

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
            'is_passed': is_passed,
            'gt': gt,
            'ga': ga,
            'strategy_fit': strategy_fit,
        }

    @staticmethod
    def _extract_parsed_ids(batch_results: List[Dict[str, Any]]) -> set:
        """AI keyword_id'yi string döndürebilir → int'e zorla."""
        parsed_ids = set()
        for r in batch_results:
            if isinstance(r, dict) and r.get('keyword_id') is not None:
                try:
                    parsed_ids.add(int(r['keyword_id']))
                except (ValueError, TypeError):
                    pass
        return parsed_ids

    def _make_intent_fallback(
        self, kw: Dict[str, Any], channel: str, reason: str
    ) -> Dict[str, Any]:
        """Fallback intent sonucu — is_fallback ile GÖRÜNÜR (source='fallback').

        SEO'da gt/ga taşımaz (NULL kalır → seçimde 0 sayılır) ve SEO override
        sayesinde is_passed=True olur — kelime elenmez, sadece boost almaz.
        """
        fallback_intent = CHANNEL_ACCEPTED_INTENTS.get(channel, ['informational'])[0]
        fallback_confidence = INTENT_FALLBACK_CONFIDENCE.get(channel, 0.43)
        return {
            'keyword_id': kw['id'],
            'intent_type': fallback_intent,
            'confidence': fallback_confidence,
            'reasoning': reason,
            'is_fallback': True,
        }

    def _retry_missing_intent(
        self,
        missing_keywords: List[Dict[str, Any]],
        channel: str,
        brand_terms: Optional[List[str]],
        product_definition: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Eksik-ID kurtarması (prefilter kalıbının portu):
        alt-küme x INTENT_MISSING_MAX_RETRIES + sınırlı tek-kelimelik final pass.
        """
        if not missing_keywords:
            return []

        pending = list(missing_keywords)
        recovered: List[Dict[str, Any]] = []

        for attempt in range(1, INTENT_MISSING_MAX_RETRIES + 1):
            if not pending:
                break
            try:
                attempted_count = len(pending)
                prompt = self._build_intent_prompt(
                    pending, channel=channel, brand_terms=brand_terms,
                    product_definition=product_definition,
                )
                response = self._complete_json(prompt, channel)
                batch_results = self._parse_intent_json(response)
                pending_ids = {kw['id'] for kw in pending}
                valid = [
                    r for r in batch_results
                    if isinstance(r, dict)
                    and self._coerce_result_id(r) in pending_ids
                ]
                recovered.extend(valid)
                recovered_ids = {self._coerce_result_id(r) for r in valid}
                pending = [kw for kw in pending if kw['id'] not in recovered_ids]
                if pending:
                    mark_attempt_failed(
                        self.ai_service,
                        f"missing_results:{len(pending)}/{attempted_count}",
                    )
                    logger.warning(
                        f"Intent {channel} missing retry {attempt}/"
                        f"{INTENT_MISSING_MAX_RETRIES}: {len(pending)} keyword eksik"
                    )
            except AiBudgetExhausted:
                logger.warning(f"Intent {channel} missing retry: bütçe tükendi")
                return recovered
            except BudgetError:
                # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                raise
            except Exception as e:
                mark_attempt_failed(self.ai_service, f"parse_error: {e}")
                logger.warning(
                    f"Intent {channel} missing retry {attempt} hata: {e}"
                )
                time.sleep(1.0 * attempt + random.uniform(0, 0.5))

        # Tek-kelimelik final pass — SINGLE_RETRY_MAX_PER_STAGE çağrı-başına
        # toplam sınır; öncelik (rank_in_channel, keyword_id)
        if pending:
            pending.sort(key=lambda kw: (kw.get('rank') or 999999, kw['id']))
            for kw in pending:
                if self._single_retries_used >= SINGLE_RETRY_MAX_PER_STAGE:
                    continue
                self._single_retries_used += 1
                try:
                    prompt = self._build_intent_prompt(
                        [kw], channel=channel, brand_terms=brand_terms,
                        product_definition=product_definition,
                    )
                    response = self._complete_json(prompt, channel)
                    batch_results = self._parse_intent_json(response)
                    valid = [
                        r for r in batch_results
                        if isinstance(r, dict)
                        and self._coerce_result_id(r) == kw['id']
                    ]
                    if valid:
                        recovered.extend(valid)
                    else:
                        mark_attempt_failed(self.ai_service, "missing_results:1/1")
                except AiBudgetExhausted:
                    break
                except BudgetError:
                    # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                    # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                    raise
                except Exception as e:
                    mark_attempt_failed(self.ai_service, f"parse_error: {e}")
                    logger.warning(
                        f"Intent {channel} tekil retry hata (kw={kw['id']}): {e}"
                    )

        return recovered

    @staticmethod
    def _coerce_result_id(result: Dict[str, Any]) -> Optional[int]:
        try:
            return int(result.get('keyword_id'))
        except (TypeError, ValueError):
            return None

    def _batch_analyze_intent(
        self,
        keywords: List[Dict[str, Any]],
        batch_size: int = INTENT_BATCH_SIZE,
        channel: str = 'SEO',
        brand_terms: Optional[List[str]] = None,
        product_definition: Optional[str] = None,
        progress_callback=None,
    ) -> List[Dict[str, Any]]:
        """
        Kelimeleri batch halinde AI'a gönderir.
        Rate-limit 3 deneme; PARSE hataları da 1 tam-batch retry alır
        (eski davranış: parse hatası anında fallback'ti). Eksik ID'ler
        _retry_missing_intent ile kurtarılır; ancak sonra fallback.
        """
        INTENT_MAX_RETRIES = 3
        INTENT_BASE_DELAY = 2  # seconds
        RETRIABLE_PATTERNS = ["429", "503", "overloaded", "Resource exhausted", "rate_limit"]

        all_results = []

        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]
            prompt = self._build_intent_prompt(
                batch, channel=channel, brand_terms=brand_terms,
                product_definition=product_definition,
            )

            # Codex v8-4: ayni-prompt retry zinciri telemetride TEK
            # mantiksal cagridir (sabit request_id + artan attempt)
            with logical_request(self.ai_service):
                for attempt in range(INTENT_MAX_RETRIES):
                    try:
                        response = self._complete_json(prompt, channel)

                        logger.debug(f"Raw AI Response: {str(response)[:200]}...")
                        batch_results = self._parse_intent_json(response)

                        logger.debug(f"Parsed {len(batch_results)} results")
                        all_results.extend(batch_results)

                        # Eksik ID'ler: anında fallback DEĞİL — hedefli kurtarma
                        parsed_ids = self._extract_parsed_ids(batch_results)
                        missing = [kw for kw in batch if kw['id'] not in parsed_ids]
                        if missing:
                            mark_attempt_failed(
                                self.ai_service,
                                f"missing_results:{len(missing)}/{len(batch)}",
                            )
                            logger.warning(
                                f"Intent {channel} partial parse: "
                                f"{len(missing)}/{len(batch)} keyword eksik, targeted retry"
                            )
                            recovered = self._retry_missing_intent(
                                missing, channel, brand_terms, product_definition
                            )
                            all_results.extend(recovered)
                            recovered_ids = self._extract_parsed_ids(recovered)
                            for kw in missing:
                                if kw['id'] not in recovered_ids:
                                    all_results.append(self._make_intent_fallback(
                                        kw, channel,
                                        'AI yanıtında bu keyword eksik (retry sonrası), varsayılan değer',
                                    ))

                        if progress_callback:
                            progress_callback(channel, 1)
                        break

                    except AiBudgetExhausted:
                        # HARD bütçe bitti — batch kanal fallback intent'ine düşer
                        logger.warning(
                            f"Intent {channel} bütçe tükendi: "
                            f"{len(batch)} keyword fallback intent alıyor"
                        )
                        if progress_callback:
                            progress_callback(channel, 1)
                        for kw in batch:
                            all_results.append(self._make_intent_fallback(
                                kw, channel, 'Expansion AI bütçesi tükendi',
                            ))
                        break

                    except BudgetError:
                        # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                        # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                        raise
                    except Exception as e:
                        error_str = str(e)
                        # Plan G: taşıma katmanı hataları (client-closed,
                        # connection reset...) rate-limit gibi TAM retriable —
                        # tek denemede fallback'e düşmez
                        is_transport = is_transport_error(e)
                        is_retriable = is_transport or any(
                            p in error_str for p in RETRIABLE_PATTERNS
                        )
                        mark_attempt_failed(
                            self.ai_service,
                            ("transport-error: " if is_transport else "parse_error: ")
                            + error_str,
                        )

                        # Parse hataları 1 retry alır (attempt==0); rate-limit
                        # ve transport kalıpları 3 denemeyi korur.
                        if (is_retriable or attempt == 0) and attempt < INTENT_MAX_RETRIES - 1:
                            retry_label = (
                                "transport-error" if is_transport
                                else "retriable" if is_retriable
                                else "parse-error"
                            )
                            delay = INTENT_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                            logger.warning(
                                f"Intent retry {attempt+1}/{INTENT_MAX_RETRIES} "
                                f"({retry_label}) "
                                f"in {delay:.1f}s: {error_str[:100]}"
                            )
                            time.sleep(delay)
                            continue

                        # Son deneme — fallback (is_fallback ile görünür)
                        logger.error(
                            f"Intent Analysis Failed (attempt {attempt+1}/{INTENT_MAX_RETRIES}): {e}"
                        )
                        failed_response = locals().get("response")
                        response_preview = (
                            str(failed_response)[:500]
                            if failed_response is not None
                            else "No response received"
                        )
                        logger.error(f"Content that failed: {response_preview}")
                        if progress_callback:
                            progress_callback(channel, 1)
                        for kw in batch:
                            all_results.append(self._make_intent_fallback(
                                kw, channel,
                                f'AI çağrısı başarısız ({attempt+1} deneme): {str(e)}',
                            ))
                        break

        return all_results
    
    def _build_intent_prompt(
        self,
        keywords: List[Dict[str, Any]],
        channel: str = "SEO",
        brand_terms: Optional[List[str]] = None,
        product_definition: Optional[str] = None,
    ) -> str:
        """Niyet analizi için prompt oluşturur."""
        keyword_list = "\n".join([f"- {kw['id']}: {kw['keyword']}" for kw in keywords])

        brand_hint = ""
        if brand_terms:
            terms_joined = ", ".join(brand_terms[:10])
            brand_hint = f"""
MÜŞTERİNİN KENDİ MARKASI: {terms_joined}
- Bu markayı içeren aramalar müşterinin KENDİ marka trafiğidir; başka siteye yönelim değildir.
- Marka + ürün/nitelik içeren sorguları transactional/commercial olarak değerlendir.
- "navigational" etiketini yalnızca BAŞKA markaların/sitelerin aramalarına ver."""

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
            product_block = ""
            if product_definition:
                product_block = f"""

MÜŞTERİ ÜRÜN TANIMI:
{product_definition}"""
            if self._algorithm_version == "v2_1":
                # v2.1 (Faz D): gt yerine S_G — icerik stratejisi beyanina uyum
                strategy_block = (
                    self._strategy_declaration
                    or "(beyan sağlanmadı — genel değerlendir)"
                )
                channel_hint = f"""
KANAL BAĞLAMI (SEO):
- Rehber, nasıl yapılır, nedir, karşılaştırma ve açıklama odaklı sorgularda informational/commercial kullan.
- Satın alma niyeti netse transactional kullan.{product_block}

İÇERİK STRATEJİSİ BEYANI (müşterinin SEO içerik önceliği):
{strategy_block}

SEO NİYET DERECELERİ (her kelime için ek olarak ver):
- strategy_fit (0 veya 1): Bu kelime, müşterinin İÇERİK STRATEJİSİ BEYANINA uyuyor mu?
  (Beyanı referans al; kelimenin klasik niyet tipinden BAĞIMSIZ değerlendir.)
- ga (0 veya 1): Ürün-kategori araması — aranan şey müşterinin ürün kategorisinde bir çözüm mü?
  (Yukarıdaki müşteri ürün tanımını referans al; tanım yoksa genel değerlendir.)"""
            else:
                channel_hint = f"""
KANAL BAĞLAMI (SEO):
- Rehber, nasıl yapılır, nedir, karşılaştırma ve açıklama odaklı sorgularda informational/commercial kullan.
- Satın alma niyeti netse transactional kullan.{product_block}

SEO NİYET DERECELERİ (her kelime için ek olarak ver):
- gt (0 veya 1): Satın alma niyeti — arayan kişi satın alma/işlem kararının eşiğinde mi?
- ga (0 veya 1): Ürün-kategori araması — aranan şey müşterinin ürün kategorisinde bir çözüm mü?
  (Yukarıdaki müşteri ürün tanımını referans al; tanım yoksa genel değerlendir.)"""

        # SEO yanıt şemasına gt/ga (v2) veya strategy_fit/ga (v2_1) eklenir.
        # Not: schema_line f-string DEĞİL — tek brace interpolasyonla aynen geçer.
        if channel == "SEO" and self._algorithm_version == "v2_1":
            schema_line = '{"keyword_id": 1, "intent_type": "informational", "confidence": 0.9, "strategy_fit": 1, "ga": 0, "reasoning": "..."}'
            extra_required = "\n- SEO için her kelimede strategy_fit ve ga alanları 0 veya 1 (integer) olmalı."
        elif channel == "SEO":
            schema_line = '{"keyword_id": 1, "intent_type": "transactional", "confidence": 0.9, "gt": 1, "ga": 0, "reasoning": "..."}'
            extra_required = "\n- SEO için her kelimede gt ve ga alanları 0 veya 1 (integer) olmalı."
        else:
            schema_line = '{"keyword_id": 1, "intent_type": "transactional", "confidence": 0.9, "reasoning": "..."}'
            extra_required = ""

        prompt = f"""Aşağıdaki anahtar kelimelerin kullanıcı niyetini analiz et.

Her kelime için şu niyet tiplerinden birini belirle:
- transactional: Satın alma niyeti (ör: "laptop satın al", "en ucuz telefon")
- informational: Bilgi arayışı (ör: "python nedir", "grip belirtileri")
- navigational: Marka/site yönelimi (ör: "facebook giriş", "amazon")
- commercial: Araştırma + potansiyel satın alma (ör: "en iyi laptop", "iphone vs samsung")
- trend_worthy: Viral/trend potansiyeli (ör: "yeni tiktok trendi", "viral challenge")
{brand_hint}{channel_hint}

Anahtar Kelimeler:
{keyword_list}

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:

[
  {schema_line},
  ...
]

ZORUNLU:
- Girdideki her keyword_id çıktı listesinde tam 1 kez olmalı.
- keyword_id integer dönmeli.{extra_required}
"""
        return prompt
