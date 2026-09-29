"""Corpus screening provider sınırı + Gemini/DeepSeek adapter'ları (plan_ai §6).

Sözleşme (plan_ai §6, tamamı adapter'da karşılanır):
- UsageCollector'a `stage="corpus_screening"` ile kayıt (retry'lar ayrı event)
- provider/model/prompt_version kaydı
- logical request + attempt numarası (collector.logical_request kapsamı)
- timeout / rate-limit / transport / response hatalarının AYRI sınıfları
- JSON parse (ai_json kurtarma zinciri) + Pydantic doğrulaması
- boş content ayrı hata sınıfı (DeepSeek JSON mode zaman zaman boş döndürür;
  Gemini response_schema garantisi VARSAYILMAZ)
- client lifecycle + idempotent close()
- GERÇEK maliyet: provider'ın döndürdüğü usage alanlarından hesaplanır
  (DeepSeek'te cache-hit/miss input token'ları AYRI fiyatlanır)

Not: bu katman KARAR vermez — yalnız model çıktısını normalize eder.
Eksik ID / retry / çözülemeyen keyword politikası runner'dadır (plan_ai §2:
terminal eleme yok).
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_list
from app.core.screening.contract import (
    DEEPSEEK_THINKING,
    GEMINI_THINKING_LEVEL,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    TEMPERATURE,
    ScreeningItem,
    build_prompt,
    validate_reason_code_membership,
)

SCREENING_STAGE = "corpus_screening"


# ── Hata sınıfları (timeout/rate-limit/transport/response ayrımı) ────────
class ScreeningProviderError(RuntimeError):
    """Provider katmanı hatası; `error_class` retry bütçesini belirler.

    `usage`: başarısız denemenin TOKEN KULLANIMI (varsa) — sağlayıcı 200
    döndürüp içerik boş bıraktığında veya çıktı parse edilemediğinde token
    ZATEN FATURALANIR; maliyet raporu bunları saymazsa gerçeğin altında
    kalır (Codex bulgu #4).
    """

    retriable = True
    reason_code = "provider_error"
    error_class = "transient"  # transient | parse | fatal

    def __init__(self, *args, usage=None):
        super().__init__(*args)
        self.usage = usage


class ScreeningTimeoutError(ScreeningProviderError):
    reason_code = "timeout"
    error_class = "transient"


class ScreeningRateLimitError(ScreeningProviderError):
    reason_code = "rate_limit"
    error_class = "transient"


class ScreeningTransportError(ScreeningProviderError):
    reason_code = "transport"
    error_class = "transient"


class ScreeningEmptyContentError(ScreeningProviderError):
    """Sağlayıcı 200 döndü ama içerik boş (DeepSeek JSON mode'da görülür)."""

    reason_code = "empty_content"
    error_class = "parse"


class ScreeningResponseError(ScreeningProviderError):
    """Bloklanmış/kesilmiş/anlamsız yanıt — parse edilemez."""

    reason_code = "response_error"
    error_class = "parse"


class ScreeningAuthError(ScreeningProviderError):
    """Anahtar/yetki hatası — retry ANLAMSIZ."""

    retriable = False
    reason_code = "auth"
    error_class = "fatal"


@dataclass
class ScreeningUsage:
    """Tek istek için ham kullanım (provider'dan geldiği gibi)."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    thoughts_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[int] = None


@dataclass
class ScreeningBatchResult:
    """Bir batch isteğinin normalize edilmiş sonucu."""

    items: List[ScreeningItem] = field(default_factory=list)
    membership_violations: Dict[int, List[str]] = field(default_factory=dict)
    invalid_items: List[Dict[str, Any]] = field(default_factory=list)
    usage: Optional[ScreeningUsage] = None
    raw_text: str = ""

    @property
    def returned_ids(self) -> set:
        return {item.id for item in self.items}


def parse_screening_payload(raw: str) -> ScreeningBatchResult:
    """Ham metni ScreeningItem listesine çevirir (fail-soft, item bazında).

    Codex iç-inceleme dersi: TEK bozuk item tüm batch'i düşürmemeli —
    geçerli item'lar korunur, bozuklar `invalid_items` olarak raporlanır ve
    runner tarafından eksik-ID retry'ına konu olur.
    """
    if not raw or not raw.strip():
        raise ScreeningEmptyContentError("provider boş içerik döndürdü")
    try:
        rows = parse_ai_json_list(raw, result_keys=("results",))
    except AIJsonParseError as exc:
        raise ScreeningResponseError(f"JSON parse edilemedi: {exc}") from exc

    result = ScreeningBatchResult(raw_text=raw)
    for row in rows:
        try:
            item = ScreeningItem.model_validate(row)
        except Exception as exc:
            result.invalid_items.append({
                "row": row,
                "error": str(exc)[:200],
            })
            continue
        violations = validate_reason_code_membership(item)
        if violations:
            # İhlal fit'i DÜŞÜRMEZ (plan_ai §2) — yalnız raporlanır
            result.membership_violations[item.id] = violations
        result.items.append(item)
    return result


class CorpusScreeningProvider(ABC):
    """Provider-neutral sınır (plan_ai §6)."""

    provider_name: str = "abstract"

    def __init__(self, *, model: str, collector=None,
                 temperature: Optional[float] = None,
                 prompt_version: Optional[str] = None):
        self.model = model
        self.collector = collector
        # Prompt sürümü deney değişkenidir; verilmezse üretim varsayılanı
        self.prompt_version = prompt_version or PROMPT_VERSION
        # Dondurulmuş sözleşme değeri VARSAYILAN; override YALNIZ teşhis
        # koşularında verilir ve manifest'e açıkça yazılır (freeze bozulmaz)
        self.temperature = TEMPERATURE if temperature is None else temperature

    @abstractmethod
    def _complete(self, prompt: str) -> tuple:
        """(raw_text, ScreeningUsage) — tek istek, retry YOK (runner'da)."""

    def build_batch_prompt(self, context: Dict[str, str],
                           keywords: List[Dict[str, Any]]) -> str:
        return build_prompt(
            product_definition=context["product_definition"],
            content_strategy=context["content_strategy"],
            target_audience=context.get("target_audience") or "-",
            social_mode=context["social_mode"],
            keywords=keywords,
            prompt_version=self.prompt_version,
        )

    def prompt_size_bytes(self, context: Dict[str, str],
                          keywords: List[Dict[str, Any]]) -> int:
        """Bütçe rezervasyonu için GERÇEK prompt boyutu (UTF-8 bayt).

        Bayt sayısı token sayısının matematiksel üst sınırıdır (token >= 1
        bayt) — sabit bir tahmin yerine bunu kullanmak tavanı gerçek kılar.
        """
        return len(self.build_batch_prompt(context, keywords).encode("utf-8"))

    def screen_batch(self, context: Dict[str, str],
                     keywords: List[Dict[str, Any]]) -> ScreeningBatchResult:
        """Tek batch: prompt kur → istek → normalize. İstisnalar tipli."""
        prompt = self.build_batch_prompt(context, keywords)
        raw, usage = self._complete(prompt)
        try:
            result = parse_screening_payload(raw)
        except ScreeningProviderError as exc:
            # Parse başarısız olsa da token FATURALANDI — usage'ı hataya
            # iliştir ki maliyet muhasebesi eksik kalmasın (Codex #4)
            exc.usage = usage
            raise
        result.usage = usage
        return result

    def _record(self, usage: Optional[ScreeningUsage], *,
                retry_reason: Optional[str] = None) -> None:
        """Telemetri (fail-open) — stage=corpus_screening, model bazlı."""
        if self.collector is None:
            return
        try:
            self.collector.record(
                stage=SCREENING_STAGE,
                model=self.model,
                prompt_tokens=usage.prompt_tokens if usage else None,
                candidates_tokens=usage.completion_tokens if usage else None,
                thoughts_tokens=usage.thoughts_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                finish_reason=usage.finish_reason if usage else None,
                latency_ms=usage.latency_ms if usage else None,
                retry_reason=retry_reason,
                cache_status=(
                    "hit" if usage and (usage.cache_hit_tokens or 0) > 0
                    else None
                ),
            )
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(f"screening telemetri kaydi basarisiz: {exc}")

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "temperature": self.temperature,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }

    def close(self) -> None:
        """Idempotent — alt sınıflar override eder."""


class GeminiCorpusScreeningProvider(CorpusScreeningProvider):
    """google-genai adapter'ı (Gemini 3.8 thinking = low, response_schema ile)."""

    provider_name = "gemini"

    def __init__(self, *, model: str, api_key: Optional[str] = None,
                 collector=None, timeout_s: float = 120.0,
                 temperature: Optional[float] = None):
        super().__init__(model=model, collector=collector,
                         temperature=temperature)
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import os

            from google import genai

            key = self._api_key or os.getenv("GEMINI_API_KEY")
            if not key:
                raise ScreeningAuthError("GEMINI_API_KEY yok")
            self._client = genai.Client(api_key=key)
        return self._client

    def _complete(self, prompt: str) -> tuple:
        from google.genai import types as genai_types

        started = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=self.temperature,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_level=GEMINI_THINKING_LEVEL
                    ),
                ),
            )
        except Exception as exc:
            err = _classify_exception(exc)
            self._record(None, retry_reason=f"{err.reason_code}: {exc}"[:200])
            raise err from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        meta = getattr(response, "usage_metadata", None)
        candidates = getattr(response, "candidates", None) or []
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        usage = ScreeningUsage(
            prompt_tokens=getattr(meta, "prompt_token_count", None),
            completion_tokens=getattr(meta, "candidates_token_count", None),
            thoughts_tokens=getattr(meta, "thoughts_token_count", None),
            total_tokens=getattr(meta, "total_token_count", None),
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            latency_ms=latency_ms,
        )
        text = getattr(response, "text", None)
        if not text or not str(text).strip():
            self._record(usage, retry_reason="empty_content")
            raise ScreeningEmptyContentError(
                "Gemini boş metin döndürdü", usage=usage
            )
        self._record(usage)
        return str(text), usage

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(f"Gemini screening client kapatma hatasi: {exc}")


class DeepSeekCorpusScreeningProvider(CorpusScreeningProvider):
    """DeepSeek (OpenAI-uyumlu) adapter'ı — non-thinking + JSON mode.

    'OpenAI uyumlu' olması ince HTTP çağrısını yeterli KILMAZ (plan_ai §6):
    hata sınıflandırması, boş-content tespiti, cache-hit/miss token ayrımı
    ve gerçek maliyet burada ele alınır.
    """

    provider_name = "deepseek"
    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(self, *, model: str, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, collector=None,
                 timeout_s: float = 120.0,
                 temperature: Optional[float] = None,
                 prompt_version: Optional[str] = None):
        super().__init__(model=model, collector=collector,
                         temperature=temperature,
                         prompt_version=prompt_version)
        self._api_key = api_key
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import os

            import httpx

            key = self._api_key or os.getenv("DEEPSEEK_API_KEY")
            if not key:
                raise ScreeningAuthError("DEEPSEEK_API_KEY yok")
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_s,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _complete(self, prompt: str) -> tuple:
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "thinking": DEEPSEEK_THINKING,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            response = self.client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            self._record(None, retry_reason=f"timeout: {exc}"[:200])
            raise ScreeningTimeoutError(str(exc)) from exc
        except httpx.TransportError as exc:
            self._record(None, retry_reason=f"transport: {exc}"[:200])
            raise ScreeningTransportError(str(exc)) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code in (401, 403):
            self._record(None, retry_reason=f"auth: {response.status_code}")
            raise ScreeningAuthError(f"DeepSeek yetki hatasi {response.status_code}")
        if response.status_code == 429:
            self._record(None, retry_reason="rate_limit: 429")
            raise ScreeningRateLimitError("DeepSeek rate limit (429)")
        if response.status_code >= 500:
            self._record(None, retry_reason=f"transport: {response.status_code}")
            raise ScreeningTransportError(f"DeepSeek {response.status_code}")
        if response.status_code >= 400:
            self._record(None, retry_reason=f"response_error: {response.status_code}")
            raise ScreeningResponseError(
                f"DeepSeek {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._record(None, retry_reason=f"response_error: {exc}"[:200])
            raise ScreeningResponseError(f"DeepSeek gövdesi JSON değil: {exc}") from exc

        usage_raw = body.get("usage") or {}
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        finish_reason = choices[0].get("finish_reason") if choices else None
        usage = ScreeningUsage(
            prompt_tokens=usage_raw.get("prompt_tokens"),
            completion_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
            cache_hit_tokens=usage_raw.get("prompt_cache_hit_tokens"),
            cache_miss_tokens=usage_raw.get("prompt_cache_miss_tokens"),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )
        content = message.get("content")
        if not content or not str(content).strip():
            self._record(usage, retry_reason="empty_content")
            raise ScreeningEmptyContentError(
                "DeepSeek boş içerik döndürdü", usage=usage
            )
        self._record(usage)
        return str(content), usage

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(f"DeepSeek client kapatma hatasi: {exc}")


def _classify_exception(exc: Exception) -> ScreeningProviderError:
    """Sağlayıcı istisnasını tipli hataya çevirir (timeout/rate/transport)."""
    from app.generators.ai_service import is_transport_error

    text = str(exc).lower()
    if "timeout" in text or "deadline" in text:
        return ScreeningTimeoutError(str(exc))
    if "429" in text or "rate limit" in text or "resource_exhausted" in text:
        return ScreeningRateLimitError(str(exc))
    if "api key" in text or "unauthorized" in text or "permission" in text:
        return ScreeningAuthError(str(exc))
    if is_transport_error(exc):
        return ScreeningTransportError(str(exc))
    return ScreeningResponseError(str(exc))
