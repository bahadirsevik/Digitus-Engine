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
    IntentAnalysis, ChannelCandidate, Keyword, PreFilterResult
)
from app.generators.ai_service import AIService
from app.core.constants import (
    PREFILTER_BATCH_SIZE,
    PREFILTER_MAX_RETRIES,
    PREFILTER_MISSING_MAX_RETRIES,
    PREFILTER_BASE_DELAY,
    PREFILTER_RETRIABLE_PATTERNS,
    ADS_HARD_NEGATIVE_TERMS,
)
from app.core.logging.config import get_task_logger

logger = get_task_logger()


class BasePreFilter(ABC):
    """Tüm kanal pre-filter'larının base class'ı."""

    CHANNEL: str  # Alt sınıfta override edilecek

    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service

    # ═══════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════

    def filter_candidates(
        self,
        scoring_run_id: int,
        keyword_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        AI pre-filtreleme çalıştırır.

        Args:
            scoring_run_id: Skorlama çalıştırma ID
            keyword_ids: Belirli keyword ID'leri (cross-channel transfer için).
                         None ise intent geçen tüm adaylar filtrelenir.

        Returns:
            {"kept": N, "eliminated": N, "fallback": N, "total": N}
        """
        logger.info(
            f"{self.CHANNEL} pre-filter başlıyor: "
            f"scoring_run_id={scoring_run_id}, "
            f"keyword_ids={'specific(' + str(len(keyword_ids)) + ')' if keyword_ids else 'all passed'}"
        )
        if keyword_ids:
            keywords = self._get_specific_keywords(keyword_ids)
        else:
            keywords = self._get_passed_keywords(scoring_run_id)

        if not keywords:
            logger.warning(f"{self.CHANNEL} pre-filter: 0 keyword bulundu, atlanıyor")
            return {"kept": 0, "eliminated": 0, "fallback": 0, "total": 0}

        logger.info(f"{self.CHANNEL} pre-filter: {len(keywords)} keyword işlenecek")
        results = self._batch_filter(keywords)
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
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )
        return [{"id": kw.id, "keyword": kw.keyword} for _, kw in rows]

    def _get_specific_keywords(self, keyword_ids: List[int]) -> List[Dict]:
        """Belirli keyword'leri alır (transfer için)."""
        keywords = (
            self.db.query(Keyword)
            .filter(Keyword.id.in_(keyword_ids))
            .all()
        )
        return [{"id": kw.id, "keyword": kw.keyword} for kw in keywords]

    # ═══════════════════════════════════════════════════════════
    # AI Batch Processing (Retry + Partial Parse + Fail-Open)
    # ═══════════════════════════════════════════════════════════

    def _batch_filter(self, keywords: List[Dict]) -> List[Dict]:
        """Keyword'leri batch'lere böler ve AI'ya gönderir."""
        all_results = []
        for i in range(0, len(keywords), PREFILTER_BATCH_SIZE):
            batch = keywords[i:i + PREFILTER_BATCH_SIZE]
            batch_results = self._process_single_batch(batch)
            all_results.extend(batch_results)
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
        batch_ids = {kw["id"] for kw in batch}

        for attempt in range(PREFILTER_MAX_RETRIES + 1):
            try:
                prompt = self._build_filter_prompt(batch)
                raw_response = self.ai_service.complete_json(prompt)

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

            except Exception as e:
                if attempt < PREFILTER_MAX_RETRIES:
                    error_str = str(e)
                    is_retriable = any(
                        p in error_str for p in PREFILTER_RETRIABLE_PATTERNS
                    )
                    delay = PREFILTER_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"{self.CHANNEL} pre-filter retry "
                        f"{attempt + 1}/{PREFILTER_MAX_RETRIES} "
                        f"({'retriable' if is_retriable else 'parse-error'}), "
                        f"backoff {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    continue

                # Tüm retry'lar tükendi → fail-open
                logger.warning(
                    f"{self.CHANNEL} pre-filter FAIL-OPEN "
                    f"({len(batch)} keyword geçiriliyor): {e}"
                )
                return [
                    self._make_fallback(kw, f"FALLBACK: {str(e)[:200]}")
                    for kw in batch
                ]

    def _retry_missing_keywords(self, keywords: List[Dict]) -> List[Dict]:
        """
        Retry only missing/broken subset from a partial parse.
        Max PREFILTER_MISSING_MAX_RETRIES attempts.
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
                raw_response = self.ai_service.complete_json(prompt)
                parsed_json = self._safe_parse_json(raw_response)
                parsed_results = self._parse_ai_response(parsed_json, pending)

                pending_ids = {kw["id"] for kw in pending}
                valid = [r for r in parsed_results if r.get("keyword_id") in pending_ids]
                recovered.extend(valid)

                recovered_ids = {r["keyword_id"] for r in valid}
                pending = [kw for kw in pending if kw["id"] not in recovered_ids]

                if pending:
                    logger.warning(
                        f"{self.CHANNEL} missing retry {attempt}/"
                        f"{PREFILTER_MISSING_MAX_RETRIES}: "
                        f"{len(pending)} keyword hala eksik"
                    )
            except Exception as e:
                delay = 1.0 * attempt + random.uniform(0, 0.5)
                logger.warning(
                    f"{self.CHANNEL} missing retry {attempt}/"
                    f"{PREFILTER_MISSING_MAX_RETRIES} failed, "
                    f"backoff {delay:.1f}s: {e}"
                )
                time.sleep(delay)

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
            "ai_reasoning": reason,
            "extra_data": {"reason_code": "FALLBACK_QUARANTINE"},
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
        """
        for r in results:
            existing = self.db.query(PreFilterResult).filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.keyword_id == r["keyword_id"],
                PreFilterResult.channel == self.CHANNEL
            ).first()

            if existing:
                # UPDATE — ikinci pass veya re-run
                existing.is_kept = r["is_kept"]
                existing.label = r.get("label")
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
        """Özet istatistikler."""
        kept = sum(1 for r in results if r["is_kept"])
        fallback = sum(1 for r in results if r.get("is_fallback"))
        return {
            "kept": kept,
            "eliminated": len(results) - kept,
            "fallback": fallback,
            "total": len(results),
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
