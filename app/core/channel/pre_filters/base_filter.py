"""
Base Pre-Filter — Abstract base class for channel-specific AI pre-filters.

Handles:
- Batch AI calls with retry + partial parse
- Fallback quarantine policy (AI failure → keyword eliminated)
- json.loads + markdown fence cleanup (complete_json returns str)
- Keyword ID validation against batch (reject AI-hallucinated IDs)
- Upsert save logic (prevents unique constraint violations on re-run)
"""
import json
import re
import time
import random
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.database.models import (
    IntentAnalysis, ChannelCandidate, Keyword, PreFilterResult, ScoringRun
)
from app.generators.ai_service import (
    AIService,
    is_transport_error,
    logical_request,
    mark_attempt_failed,
    scoped,
)
from app.core.constants import (
    TERMINAL_PREFILTER_REASONS,
    PREFILTER_BATCH_SIZE,
    PREFILTER_MAX_RETRIES,
    PREFILTER_MISSING_MAX_RETRIES,
    PREFILTER_BASE_DELAY,
    PREFILTER_RETRIABLE_PATTERNS,
    PREFILTER_MAX_TOKENS,
    SINGLE_RETRY_MAX_PER_STAGE,
    FALLBACK_QUARANTINE_REASON,
    ADS_HARD_NEGATIVE_TERMS,
)
from app.core.channel.ai_budget import AiBudgetExhausted, AiCallBudget
from app.core.telemetry.ai_cost_budget import BudgetError
from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON
from app.core.channel.brand_defense import (
    load_brand_defense_context, is_own_brand_keyword,
    load_product_definition,
    BRAND_DEFENSE_LABEL, BRAND_DEFENSE_REASON,
)
from app.core.logging_config import get_task_logger

logger = get_task_logger()


class BasePreFilter(ABC):
    """Tüm kanal pre-filter'larının base class'ı."""

    CHANNEL: str  # Alt sınıfta override edilecek
    # Gemini structured-output şeması (brand_filter/RSA generator kalıbı) —
    # alt sınıf tanımlar; None ise şemasız çağrı yapılır.
    STAGE: str = "unknown"  # alt sinif belirler (plan C)
    RESPONSE_SCHEMA: Optional[Dict[str, Any]] = None
    MAX_TOKENS = PREFILTER_MAX_TOKENS

    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = scoped(ai_service, self.STAGE)
        # filter_candidates çağrısı başına sıfırlanan durum
        self._ai_calls_used = 0
        self._single_retries_used = 0
        self._budget: Optional[AiCallBudget] = None

    def _complete_json(self, prompt: str) -> str:
        """Tek AI çağrısı — HARD bütçe kontrolü + gerçek çağrı sayacı.

        Bütçe (expansion) bitmişse AiBudgetExhausted fırlatır; çağıran
        katman kendi fallback semantiğini uygular. batches gösterim
        metriği ayrı; bütçe hesabı YALNIZCA ai_calls_used'a dayanır.
        """
        if self._budget is not None and not self._budget.try_consume():
            raise AiBudgetExhausted(
                f"{self.CHANNEL} pre-filter: expansion AI bütçesi tükendi"
            )
        self._ai_calls_used += 1
        return self.ai_service.complete_json(
            prompt,
            max_tokens=self.MAX_TOKENS,
            response_schema=self.RESPONSE_SCHEMA,
        )

    def _pre_brand_defense_filter(
        self, keywords: List[Dict]
    ) -> tuple[List[Dict], List[Dict]]:
        """Kanal-özel deterministik ön-eleme hook'u (default no-op).

        Brand-defense ayıklamasından ÖNCE çalışır — böylece own-brand
        kelimeler de deterministik iş kurallarından (ör. SEO fiyat
        filtresi) muaf kalamaz. Dönüş: (blocked_results, remaining_keywords).
        AI kullanmaz; bütçeden bağımsız HER ZAMAN çalışır.
        """
        return [], keywords

    # ═══════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════

    def filter_candidates(
        self,
        scoring_run_id: int,
        keyword_ids: Optional[List[int]] = None,
        progress_callback=None,
        budget: Optional[AiCallBudget] = None,
    ) -> Dict[str, Any]:
        """
        AI pre-filtreleme çalıştırır.

        Args:
            scoring_run_id: Skorlama çalıştırma ID
            keyword_ids: Belirli keyword ID'leri (cross-channel transfer için).
                         None ise intent geçen tüm adaylar filtrelenir.
            budget: Expansion HARD AI bütçesi (None = sınırsız, ilk pipeline)

        Returns:
            {"kept": N, "eliminated": N, "fallback": N, "total": N,
             "batches": N, "ai_calls_used": N}
        """
        logger.info(
            f"{self.CHANNEL} pre-filter başlıyor: "
            f"scoring_run_id={scoring_run_id}, "
            f"keyword_ids={'specific(' + str(len(keyword_ids)) + ')' if keyword_ids else 'all passed'}"
        )
        # Çağrı-başına durum (tekil retry sınırı batch başına SIFIRLANMAZ)
        self._ai_calls_used = 0
        self._single_retries_used = 0
        self._budget = budget

        if keyword_ids:
            keywords = self._get_specific_keywords(scoring_run_id, keyword_ids)
        else:
            keywords = self._get_passed_keywords(scoring_run_id)

        if not keywords:
            logger.warning(f"{self.CHANNEL} pre-filter: 0 keyword bulundu, atlanıyor")
            return {
                "kept": 0, "eliminated": 0, "fallback": 0, "total": 0,
                "batches": 0, "ai_calls_used": 0,
            }

        # Marka savunma: kendi marka terimleri AI'a hic gonderilmez,
        # deterministik olarak korunur (confirmed profil yoksa no-op).
        run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        brand_ctx = load_brand_defense_context(self.db, run)

        # v2.1 (Faz D): prompt builder baglami — v2 prompt'lari BAYT-AYNI kalir
        # (D-harness referansi). Codex 3. tur #1 (FAIL-CLOSED): v2_1 baglami
        # YALNIZ manifest snapshot'indan; canli load_product_definition HIC
        # cagrilmaz, snapshot yok/eksikse AI cagrisindan ONCE tipli hata.
        self._algorithm_version = getattr(run, "algorithm_version", "v2") or "v2"
        self._target_audience = None
        self._social_mode = None
        if self._algorithm_version == "v2_1":
            from app.core.policy.channel_strategy import (
                require_dispatch_strategy_context,
            )

            snapshot = require_dispatch_strategy_context(run)
            self._product_definition = snapshot["product_definition"]
            self._social_mode = snapshot.get("social_mode")
            self._target_audience = snapshot.get("target_audience")
        else:
            # Musteri urun tanimi: prompt builder'lar kullanabilir
            # (ADS sinif sorulari)
            self._product_definition = load_product_definition(self.db, run)

        # Kanal-özel deterministik ön-eleme (ör. SEO fiyat filtresi) —
        # brand-defense'tan ÖNCE: own-brand fiyat sorguları da yakalanır,
        # PRICE_TERM hiçbir durumda bypass edilmez (bütçe sıfırken bile).
        pre_blocked, keywords = self._pre_brand_defense_filter(keywords)
        if pre_blocked:
            logger.info(
                f"{self.CHANNEL} pre-filter: {len(pre_blocked)} keyword "
                f"deterministik ön-eleme ile elendi (AI'a gönderilmedi)"
            )

        own_brand_keywords: List[Dict] = []
        if brand_ctx:
            remaining = []
            for kw in keywords:
                if is_own_brand_keyword(kw.get("keyword", ""), brand_ctx, self.CHANNEL):
                    own_brand_keywords.append(kw)
                else:
                    remaining.append(kw)
            keywords = remaining
            if own_brand_keywords:
                logger.info(
                    f"{self.CHANNEL} pre-filter: {len(own_brand_keywords)} kendi-marka "
                    f"keyword brand_defense ile korundu (AI'a gonderilmedi)"
                )

        logger.info(f"{self.CHANNEL} pre-filter: {len(keywords)} keyword işlenecek")
        results = list(pre_blocked)
        results.extend(self._make_brand_defense_keep(kw) for kw in own_brand_keywords)
        if keywords:
            results.extend(self._batch_filter(keywords, progress_callback=progress_callback))
        self._save_results(scoring_run_id, results)
        summary = self._summarize(results)
        logger.info(f"{self.CHANNEL} pre-filter tamamlandı: {summary}")
        return summary

    # ═══════════════════════════════════════════════════════════
    # Data Retrieval
    # ═══════════════════════════════════════════════════════════

    def _get_passed_keywords(self, scoring_run_id: int) -> List[Dict]:
        """Intent analizini geçmiş keyword'leri alır."""
        rows = (
            self.db.query(ChannelCandidate, Keyword)
            .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
            .join(IntentAnalysis, and_(
                IntentAnalysis.keyword_id == ChannelCandidate.keyword_id,
                IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id,
                IntentAnalysis.channel == ChannelCandidate.channel
            ))
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == self.CHANNEL)
            .filter(IntentAnalysis.is_passed == True)
            .outerjoin(
                PreFilterResult,
                and_(
                    PreFilterResult.keyword_id == ChannelCandidate.keyword_id,
                    PreFilterResult.scoring_run_id == ChannelCandidate.scoring_run_id,
                    PreFilterResult.channel == ChannelCandidate.channel,
                    PreFilterResult.extra_data["reason_code"].as_string().in_(
                        TERMINAL_PREFILTER_REASONS
                    ),
                ),
            )
            .filter(PreFilterResult.id.is_(None))
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )
        # rank: tekil kurtarma çağrılarının önceliklendirilmesi için taşınır
        return [
            {"id": kw.id, "keyword": kw.keyword, "rank": candidate.rank_in_channel}
            for candidate, kw in rows
        ]

    def _get_specific_keywords(self, scoring_run_id: int, keyword_ids: List[int]) -> List[Dict]:
        """Belirli keyword'leri alır (transfer için) — rank dahil."""
        rows = (
            self.db.query(ChannelCandidate, Keyword)
            .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
            .join(IntentAnalysis, and_(
                IntentAnalysis.keyword_id == ChannelCandidate.keyword_id,
                IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id,
                IntentAnalysis.channel == ChannelCandidate.channel,
            ))
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == self.CHANNEL)
            .filter(ChannelCandidate.keyword_id.in_(keyword_ids))
            .filter(IntentAnalysis.is_passed == True)
            .all()
        )
        excluded_ids = {
            row.keyword_id
            for row in (
                self.db.query(PreFilterResult.keyword_id)
                .filter(PreFilterResult.scoring_run_id == scoring_run_id)
                .filter(PreFilterResult.channel == self.CHANNEL)
                .filter(PreFilterResult.keyword_id.in_(keyword_ids))
                .filter(PreFilterResult.extra_data["reason_code"].as_string().in_(
                    TERMINAL_PREFILTER_REASONS
                ))
                .all()
            )
        }
        return [
            {"id": kw.id, "keyword": kw.keyword, "rank": candidate.rank_in_channel}
            for candidate, kw in rows
            if kw.id not in excluded_ids
        ]

    # ═══════════════════════════════════════════════════════════
    # AI Batch Processing (Retry + Partial Parse + Fail-Open)
    # ═══════════════════════════════════════════════════════════

    def _batch_filter(self, keywords: List[Dict], progress_callback=None) -> List[Dict]:
        """Keyword'leri batch'lere böler ve AI'ya gönderir."""
        all_results = []
        for i in range(0, len(keywords), PREFILTER_BATCH_SIZE):
            batch = keywords[i:i + PREFILTER_BATCH_SIZE]
            batch_results = self._process_single_batch(batch)
            all_results.extend(batch_results)
            if progress_callback:
                progress_callback(self.CHANNEL, 1)
        return all_results

    def _process_single_batch(self, batch: List[Dict]) -> List[Dict]:
        """
        Tek batch işleme: retry + partial parse + fail-open.

        1. AI çağrısı yap
        2. Yanıtı parse et (json.loads + markdown fence cleanup)
        3. Keyword ID'leri batch'e karşı doğrula (hallucination koruması)
        4. Eksik keyword'lere fallback uygula
        5. Tamamen başarısızsa fail-open (tüm keyword'ler geçer)
        """
        # Codex v8-4: aynı-batch retry zinciri telemetride TEK mantıksal
        # çağrıdır (sabit request_id + artan attempt); iç alt-küme retry'ları
        # da aynı mantıksal kurtarmanın parçası sayılır
        with logical_request(self.ai_service):
            return self._process_single_batch_attempts(batch)

    def _process_single_batch_attempts(self, batch: List[Dict]) -> List[Dict]:
        batch_ids = {kw["id"] for kw in batch}

        for attempt in range(PREFILTER_MAX_RETRIES + 1):
            try:
                prompt = self._build_filter_prompt(batch)
                raw_response = self._complete_json(prompt)

                # complete_json() string döndürür → json.loads + fence cleanup
                parsed_json = self._safe_parse_json(raw_response)

                # Alt sınıfın parse mantığı
                parsed_results = self._parse_ai_response(parsed_json, batch)

                # Keyword ID doğrulama: sadece batch'teki ID'leri kabul et
                validated = []
                for r in parsed_results:
                    if r["keyword_id"] in batch_ids:
                        validated.append(r)
                    else:
                        logger.warning(
                            f"{self.CHANNEL} pre-filter: AI returned unknown "
                            f"keyword_id={r['keyword_id']}, ignoring"
                        )

                # Kısmi parse kontrolü: hangi keyword'ler eksik?
                validated_ids = {r["keyword_id"] for r in validated}
                missing_ids = batch_ids - validated_ids

                if not missing_ids:
                    return validated  # Tam başarı

                # Partial parse: çıkanları kabul et, eksik kalanları ayrı retry et
                mark_attempt_failed(
                    self.ai_service,
                    f"missing_results:{len(missing_ids)}/{len(batch)}",
                )
                logger.warning(
                    f"{self.CHANNEL} partial parse: "
                    f"{len(missing_ids)}/{len(batch)} keyword eksik, targeted retry"
                )
                missing_keywords = [kw for kw in batch if kw["id"] in missing_ids]
                recovered = self._retry_missing_keywords(missing_keywords)
                validated.extend(recovered)

                recovered_ids = {r["keyword_id"] for r in recovered}
                still_missing = missing_ids - recovered_ids
                for kw in missing_keywords:
                    if kw["id"] in still_missing:
                        validated.append(
                            self._make_fallback(kw, "Kısmi parse: AI yanıtında eksik")
                        )
                return validated

            except AiBudgetExhausted as e:
                # HARD bütçe bitti — retry YOK, batch doğrudan kanal
                # fallback semantiğine (karantina) gider.
                logger.warning(
                    f"{self.CHANNEL} pre-filter BÜTÇE TÜKENDİ "
                    f"({len(batch)} keyword karantinaya alınıyor): {e}"
                )
                return [
                    self._make_fallback(kw, "Expansion AI bütçesi tükendi")
                    for kw in batch
                ]
            except BudgetError:
                # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                raise
            except Exception as e:
                # Plan G: transport hataları doğru etiketle görünür
                # (prefilter zaten her hatayı PREFILTER_MAX_RETRIES kadar
                # retry'lar — etiket telemetri/log ayrımı içindir)
                is_transport = is_transport_error(e)
                mark_attempt_failed(
                    self.ai_service,
                    ("transport-error: " if is_transport else "parse_error: ")
                    + str(e),
                )
                if attempt < PREFILTER_MAX_RETRIES:
                    error_str = str(e)
                    is_retriable = any(
                        p in error_str for p in PREFILTER_RETRIABLE_PATTERNS
                    )
                    retry_label = (
                        "transport-error" if is_transport
                        else "retriable" if is_retriable
                        else "parse-error"
                    )
                    delay = PREFILTER_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"{self.CHANNEL} pre-filter retry "
                        f"{attempt + 1}/{PREFILTER_MAX_RETRIES} "
                        f"({retry_label}), "
                        f"backoff {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    continue

                # Tüm retry'lar tükendi → karantina (fail-open DEĞİL:
                # kelime is_kept=False ile dışarıda kalır, log doğruyu söyler)
                logger.warning(
                    f"{self.CHANNEL} pre-filter FALLBACK-QUARANTINE "
                    f"({len(batch)} keyword karantinaya alınıyor): {e}"
                )
                return [
                    self._make_fallback(kw, f"FALLBACK: {str(e)[:200]}")
                    for kw in batch
                ]

    def _retry_missing_keywords(self, keywords: List[Dict]) -> List[Dict]:
        """
        Retry only missing/broken subset from a partial parse.
        Max PREFILTER_MISSING_MAX_RETRIES subset attempts + sınırlı
        TEK-KELİMELİK final pass (son savunma hattı — karantinadan önce).
        """
        if not keywords:
            return []

        pending = list(keywords)
        recovered: List[Dict] = []

        for attempt in range(1, PREFILTER_MISSING_MAX_RETRIES + 1):
            if not pending:
                break
            try:
                prompt = self._build_filter_prompt(pending)
                raw_response = self._complete_json(prompt)
                parsed_json = self._safe_parse_json(raw_response)
                parsed_results = self._parse_ai_response(parsed_json, pending)

                pending_ids = {kw["id"] for kw in pending}
                valid = [r for r in parsed_results if r.get("keyword_id") in pending_ids]
                recovered.extend(valid)

                recovered_ids = {r["keyword_id"] for r in valid}
                pending = [kw for kw in pending if kw["id"] not in recovered_ids]

                if pending:
                    mark_attempt_failed(
                        self.ai_service,
                        f"missing_results:{len(pending)}/{len(pending_ids)}",
                    )
                    logger.warning(
                        f"{self.CHANNEL} missing retry {attempt}/"
                        f"{PREFILTER_MISSING_MAX_RETRIES}: "
                        f"{len(pending)} keyword hala eksik"
                    )
            except AiBudgetExhausted:
                logger.warning(
                    f"{self.CHANNEL} missing retry: bütçe tükendi, "
                    f"{len(pending)} keyword karantinaya gidecek"
                )
                return recovered
            except BudgetError:
                # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                raise
            except Exception as e:
                mark_attempt_failed(self.ai_service, f"parse_error: {e}")
                delay = 1.0 * attempt + random.uniform(0, 0.5)
                logger.warning(
                    f"{self.CHANNEL} missing retry {attempt}/"
                    f"{PREFILTER_MISSING_MAX_RETRIES} failed, "
                    f"backoff {delay:.1f}s: {e}"
                )
                time.sleep(delay)

        # Tek-kelimelik final pass: SINGLE_RETRY_MAX_PER_STAGE sınırı
        # ÇAĞRI-başına toplamdır (self._single_retries_used, filter_candidates
        # başında sıfırlanır); öncelik (rank_in_channel, keyword_id) —
        # en üst sıralı adaylar önce kurtarılır.
        if pending:
            pending.sort(key=lambda kw: (kw.get("rank") or 999999, kw["id"]))
            for kw in pending:
                if self._single_retries_used >= SINGLE_RETRY_MAX_PER_STAGE:
                    logger.warning(
                        f"{self.CHANNEL} tekil retry sınırı doldu "
                        f"({SINGLE_RETRY_MAX_PER_STAGE}); kw={kw['id']} karantinaya"
                    )
                    continue
                self._single_retries_used += 1
                try:
                    raw_response = self._complete_json(self._build_filter_prompt([kw]))
                    parsed_json = self._safe_parse_json(raw_response)
                    parsed_results = self._parse_ai_response(parsed_json, [kw])
                    valid = [
                        r for r in parsed_results
                        if r.get("keyword_id") == kw["id"]
                    ]
                    if valid:
                        recovered.extend(valid)
                    else:
                        mark_attempt_failed(self.ai_service, "missing_results:1/1")
                        logger.warning(
                            f"{self.CHANNEL} tekil retry sonuçsuz: kw={kw['id']}"
                        )
                except AiBudgetExhausted:
                    logger.warning(
                        f"{self.CHANNEL} tekil retry: bütçe tükendi (kw={kw['id']})"
                    )
                    break
                except BudgetError:
                    # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
                    # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
                    raise
                except Exception as e:
                    mark_attempt_failed(self.ai_service, f"parse_error: {e}")
                    logger.warning(
                        f"{self.CHANNEL} tekil retry hata (kw={kw['id']}): {e}"
                    )

        return recovered

    def _safe_parse_json(self, raw: str) -> dict:
        """
        AI yanıtını güvenli parse eder.
        complete_json() string döndürür, bazen markdown fence ile sarılı olabilir.
        """
        if not raw or not raw.strip():
            raise ValueError("AI boş yanıt döndürdü")

        text = raw.strip()

        # Markdown fence temizliği: ```json ... ``` veya ``` ... ```
        fence_match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?\s*```',
            text,
            re.DOTALL
        )
        if fence_match:
            text = fence_match.group(1).strip()

        # Try multiple repair candidates before failing open.
        candidates = self._json_candidates(text)
        parse_errors = []
        for cand in candidates:
            for attempt_text in (cand, self._close_unbalanced_json(cand)):
                if not attempt_text:
                    continue
                try:
                    return json.loads(attempt_text)
                except Exception as e:
                    parse_errors.append(str(e))
                    continue

        # Object-level salvage: useful when top-level JSON is malformed but
        # object chunks are still parseable.
        extracted = self._extract_object_list(text)
        if extracted:
            return extracted

        raise ValueError(
            f"JSON parse failed after repair attempts: "
            f"{parse_errors[-1] if parse_errors else 'unknown error'}"
        )

    def _json_candidates(self, text: str) -> List[str]:
        """Build parse candidates from raw text by trimming and normalizing."""
        candidates = []
        normalized = self._normalize_json_text(text)
        candidates.append(normalized)

        # First/last JSON token trimming.
        start_positions = [i for i in (normalized.find("{"), normalized.find("[")) if i >= 0]
        if start_positions:
            start = min(start_positions)
            candidates.append(self._normalize_json_text(normalized[start:]))

        end_positions = [i for i in (normalized.rfind("}"), normalized.rfind("]")) if i >= 0]
        if end_positions:
            end = max(end_positions)
            candidates.append(self._normalize_json_text(normalized[:end + 1]))

        if start_positions and end_positions:
            start = min(start_positions)
            end = max(end_positions)
            if end >= start:
                candidates.append(self._normalize_json_text(normalized[start:end + 1]))

        # Preserve order, remove empty/duplicates.
        uniq = []
        seen = set()
        for c in candidates:
            if not c:
                continue
            if c in seen:
                continue
            seen.add(c)
            uniq.append(c)
        return uniq

    def _normalize_json_text(self, text: str) -> str:
        """Apply safe JSON text normalizations."""
        cleaned = text.strip()
        cleaned = cleaned.replace("\ufeff", "")
        cleaned = cleaned.replace("“", '"').replace("”", '"')
        cleaned = cleaned.replace("’", "'").replace("‘", "'")
        # Remove trailing commas before closing brackets/braces.
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return cleaned

    def _close_unbalanced_json(self, text: str) -> str:
        """
        Try to repair truncated JSON by appending missing closing tokens.
        Naive but safe for common truncation patterns in model output.
        """
        s = text.strip()
        if not s:
            return s
        open_curly = s.count("{")
        close_curly = s.count("}")
        open_square = s.count("[")
        close_square = s.count("]")
        if open_square > close_square:
            s += "]" * (open_square - close_square)
        if open_curly > close_curly:
            s += "}" * (open_curly - close_curly)
        return s

    def _extract_object_list(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """Best-effort object-by-object extraction from malformed JSON text."""
        decoder = json.JSONDecoder()
        objs: List[Dict[str, Any]] = []
        i = 0
        n = len(text)

        while i < n:
            if text[i] != "{":
                i += 1
                continue
            try:
                obj, end = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    objs.append(obj)
                i += end
            except Exception:
                i += 1

        return objs if objs else None

    def _make_brand_defense_keep(self, kw: Dict) -> Dict:
        """Kendi marka terimi: AI'a sorulmadan deterministik keep kaydi."""
        return {
            "keyword_id": kw["id"],
            "is_kept": True,
            "label": BRAND_DEFENSE_LABEL,
            # ADS'te kendi marka terimi en yuksek oncelik sinifidir (2);
            # diger kanallarda None -> secimde DEFAULT_AI_CLASS_WHEN_MISSING.
            "ai_class": 2 if self.CHANNEL == "ADS" else None,
            "ai_reasoning": "Kendi marka terimi — savunma amacli korundu (AI'a gonderilmedi)",
            "extra_data": {"reason_code": BRAND_DEFENSE_REASON, "own_brand": True},
            "transfer_channel": None,
            "is_fallback": False,
        }

    def _make_fallback(self, kw: Dict, reason: str) -> Dict:
        """Fallback kaydı: keyword quarantine edilir, is_fallback=True."""
        is_kept = False
        kw_norm = str(kw.get("keyword", "")).lower()
        if self.CHANNEL == "ADS" and any(t in kw_norm for t in ADS_HARD_NEGATIVE_TERMS):
            reason = f"{reason} | ADS hard-negative"
        return {
            "keyword_id": kw["id"],
            "is_kept": is_kept,
            "label": None,
            "ai_class": None,  # AI karari yok — karantina
            "ai_reasoning": reason,
            "extra_data": {"reason_code": FALLBACK_QUARANTINE_REASON},
            "transfer_channel": None,
            "is_fallback": True,
        }

    # ═══════════════════════════════════════════════════════════
    # Save (Upsert — Unique Constraint Koruması)
    # ═══════════════════════════════════════════════════════════

    def _save_results(self, scoring_run_id: int, results: List[Dict]):
        """
        Sonuçları DB'ye yazar.
        UPSERT: aynı (scoring_run_id, keyword_id, channel) varsa UPDATE.
        İkinci SEO pre-filter pass'te veya re-run'da unique çakışma önlenir.

        AYNI ÇAĞRIDA tekrarlanan keyword TEK satır olur (canlı koşuda
        yakalandı): AI bir yanıtta aynı id'yi iki kez döndürdüğünde
        `autoflush=False` olduğu için ikinci arama bekleyen INSERT'i
        GÖREMİYOR, iki satır birden ekleniyor ve `uq_pre_filter` ihlali
        TÜM atama görevini düşürüyordu. İlk kayıt kazanır; tekrar
        SESSİZ DEĞİL, uyarı olarak loglanır (AI sözleşme ihlali).
        """
        seen: set = set()
        deduped: List[Dict] = []
        for r in results:
            kid = r["keyword_id"]
            if kid in seen:
                logger.warning(
                    f"PreFilter AI SÖZLEŞME İHLALİ (yinelenen id): "
                    f"kw={kid}, channel={self.CHANNEL} — ilk kayıt korundu, "
                    f"tekrar atıldı"
                )
                continue
            seen.add(kid)
            deduped.append(r)

        for r in deduped:
            existing = self.db.query(PreFilterResult).filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.keyword_id == r["keyword_id"],
                PreFilterResult.channel == self.CHANNEL
            ).first()

            if existing:
                if (existing.extra_data or {}).get("reason_code") in TERMINAL_PREFILTER_REASONS:
                    logger.debug(
                        f"PreFilter preserving terminal row: kw={r['keyword_id']}, "
                        f"channel={self.CHANNEL}"
                    )
                    continue
                # UPDATE — ikinci pass veya re-run
                existing.is_kept = r["is_kept"]
                existing.label = r.get("label")
                existing.ai_class = r.get("ai_class")
                existing.ai_reasoning = r.get("ai_reasoning")
                existing.extra_data = r.get("extra_data", {})
                existing.transfer_channel = r.get("transfer_channel")
                existing.is_fallback = r.get("is_fallback", False)
                logger.debug(
                    f"PreFilter upsert UPDATE: kw={r['keyword_id']}, "
                    f"channel={self.CHANNEL}"
                )
            else:
                # INSERT
                self.db.add(PreFilterResult(
                    scoring_run_id=scoring_run_id,
                    keyword_id=r["keyword_id"],
                    channel=self.CHANNEL,
                    is_kept=r["is_kept"],
                    label=r.get("label"),
                    ai_class=r.get("ai_class"),
                    ai_reasoning=r.get("ai_reasoning"),
                    extra_data=r.get("extra_data", {}),
                    transfer_channel=r.get("transfer_channel"),
                    is_fallback=r.get("is_fallback", False),
                ))

        self.db.commit()

    # ═══════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════

    def _summarize(self, results: List[Dict]) -> Dict[str, int]:
        """Özet istatistikler.

        batches: gösterim metriği (tahmini); ai_calls_used: GERÇEK AI çağrı
        sayısı (ilk çağrılar + parse retry + eksik-subset retry + tekil
        retry) — expansion bütçe muhasebesi YALNIZCA bunu kullanır.
        """
        kept = sum(1 for r in results if r["is_kept"])
        fallback = sum(1 for r in results if r.get("is_fallback"))
        return {
            "kept": kept,
            "eliminated": len(results) - kept,
            "fallback": fallback,
            "total": len(results),
            "batches": (len(results) + PREFILTER_BATCH_SIZE - 1) // PREFILTER_BATCH_SIZE,
            "ai_calls_used": self._ai_calls_used,
        }

    # ═══════════════════════════════════════════════════════════
    # Abstract Methods (Alt sınıfta implement edilecek)
    # ═══════════════════════════════════════════════════════════

    @abstractmethod
    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        """Kanal-özel AI prompt'u oluşturur."""
        ...

    @abstractmethod
    def _parse_ai_response(
        self, response: dict, original: List[Dict]
    ) -> List[Dict]:
        """
        AI yanıtını standart formata çevirir.

        Her sonuç dict'i şu alanları içermeli:
        - keyword_id: int
        - is_kept: bool
        - label: Optional[str]
        - ai_reasoning: str
        - extra_data: dict
        - transfer_channel: Optional[str]
        - is_fallback: bool (False olmalı — fallback base'de handle edilir)
        """
        ...
