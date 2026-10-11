"""Anthropic (Claude) kanal adaptörü — plan_claude_gemini_karsilastirma.md §4.

Sözleşme (ADAPTER_CONTRACT_VERSION mühürlenir):
- prompt: bayt-aynı tek user mesajı; yalnız şemasız json_mode'da sabit `system`.
- response_schema -> output_config.format json_schema (dönüştürücü ayrı modül).
- thinking: her zaman adaptive; derinlik output_config.effort (sabit tablo).
- temperature/top_p ASLA gönderilmez (Claude 5'te 400).
- max_tokens = min(caller_cap + THINKING_ALLOWANCE[effort], 16000); ledger
  rezervasyonu GÖNDERİLEN değerden yapılır.
- SDK iç retry kapalı (max_retries=0); retry çağıranda (motor).
- Varsayılan model yok: model route/override'tan gelir (tam API id).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

from loguru import logger

from app.config import settings
from app.core.social.anthropic_schema import to_anthropic_response_schema
from app.generators.ai_service import AIResponseError, AIService

ADAPTER_CONTRACT_VERSION = "1"
JSON_ONLY_SYSTEM = "Yalnız geçerli JSON döndür, açıklama yazma."
SYSTEM_SHA = hashlib.sha256(JSON_ONLY_SYSTEM.encode("utf-8")).hexdigest()
EFFORT_TABLE = {"low": "low", "medium": "medium", "high": "high", None: "low"}
THINKING_ALLOWANCE = {"low": 2000, "medium": 4000, "high": 8000}
HARD_MAX_TOKENS = 16000
THINKING_POLICY_SHA = hashlib.sha256(json.dumps(
    {"effort_table": {str(k): v for k, v in EFFORT_TABLE.items()},
     "allowance": THINKING_ALLOWANCE, "hard_cap": HARD_MAX_TOKENS},
    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class AnthropicContractError(AIResponseError):
    """Adaptör sözleşmesine aykırı istek (Claude'da 400 olacak) — fail-closed."""


class AnthropicRefusalError(AIResponseError):
    """stop_reason == 'refusal' (güvenlik reddi)."""


def _effort(thinking_level: Optional[str]) -> str:
    key = (thinking_level or "").strip().lower() or None
    if key not in EFFORT_TABLE:
        raise AnthropicContractError(
            f"Anthropic invalid argument: thinking_level {thinking_level!r} "
            f"tanımsız (izinli: low/medium/high)")
    return EFFORT_TABLE[key]


def _sent_max_tokens(caller_cap: int, effort: str) -> int:
    cap = int(caller_cap or 0)
    if cap <= 0:
        raise AnthropicContractError(
            "Anthropic invalid argument: max_tokens > 0 olmalı")
    return min(cap + THINKING_ALLOWANCE[effort], HARD_MAX_TOKENS)


def anthropic_request_envelope(prompt: str, *, model: str, max_tokens: int,
                               json_mode: bool,
                               response_schema: Optional[Dict[str, Any]],
                               thinking_level: Optional[str]) -> Dict[str, Any]:
    """`messages.create`'e GİDECEK birebir kwargs (saf; ağ yok)."""
    effort = _effort(thinking_level)
    output_config: Dict[str, Any] = {"effort": effort}
    env: Dict[str, Any] = {
        "model": model,
        "max_tokens": _sent_max_tokens(max_tokens, effort),
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
    }
    if json_mode and response_schema is not None:
        output_config["format"] = {
            "type": "json_schema",
            "schema": to_anthropic_response_schema(response_schema)}
    elif json_mode:
        env["system"] = JSON_ONLY_SYSTEM
    return env


def anthropic_request_input_ceiling_bytes(
        prompt: str, *, json_mode: bool,
        response_schema: Optional[Dict[str, Any]] = None) -> int:
    """Girdi için konservatif üst sınır: her token >= 1 UTF-8 bayt."""
    env = anthropic_request_envelope(
        prompt, model="", max_tokens=1, json_mode=json_mode,
        response_schema=response_schema, thinking_level=None)
    parts = {"messages": env["messages"], "system": env.get("system"),
             "format": env["output_config"].get("format")}
    return len(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


class AnthropicRateLimiter:
    """Süreç-içi token-bucket (RPM/ITPM/OTPM) + eşzamanlılık semaforu.

    Limit None = o boyut sınırsız (yalnız semafor). shortcut: tahmini giriş
    bayt/3, çıkış = gönderilen max_tokens; release gerçek usage ile farkı iade eder.
    """

    def __init__(self, rpm=None, itpm=None, otpm=None, concurrency: int = 4,
                 clock=time.monotonic, sleep=time.sleep):
        self._caps = {"r": rpm, "i": itpm, "o": otpm}
        self._level = {k: float(v) for k, v in self._caps.items() if v}
        self._last = clock()
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max(1, int(concurrency)))

    def _refill(self) -> None:
        now = self._clock()
        dt, self._last = now - self._last, now
        for k in self._level:
            cap = float(self._caps[k])
            self._level[k] = min(cap, self._level[k] + cap * dt / 60.0)

    def acquire(self, in_est: int, out_est: int):
        """(ticket, queue_wait_ms) döner."""
        start = self._clock()
        self._sem.acquire()
        want = {"r": 1.0, "i": float(in_est), "o": float(out_est)}
        # kovadan büyük istek sonsuza dek beklemesin: kova kapasitesine kırp
        cost = {k: min(v, float(self._caps[k])) for k, v in want.items()
                if k in self._level}
        while True:
            with self._lock:
                self._refill()
                short = [(cost[k] - self._level[k]) / (self._caps[k] / 60.0)
                         for k in cost if self._level[k] < cost[k]]
                if not short:
                    for k, v in cost.items():
                        self._level[k] -= v
                    break
                wait = max(short)
            self._sleep(max(wait, 0.01))
        return cost, int((self._clock() - start) * 1000)

    def release(self, ticket, in_actual: Optional[int] = None,
                out_actual: Optional[int] = None) -> None:
        with self._lock:
            for k, actual in (("i", in_actual), ("o", out_actual)):
                if k in ticket and actual is not None:
                    self._level[k] = min(
                        float(self._caps[k]),
                        self._level[k] + max(0.0, ticket[k] - float(actual)))
        self._sem.release()


_SHARED: Optional[AnthropicRateLimiter] = None
_SHARED_LOCK = threading.Lock()


def _shared_limiter() -> AnthropicRateLimiter:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = AnthropicRateLimiter(
                settings.ANTHROPIC_RPM, settings.ANTHROPIC_ITPM,
                settings.ANTHROPIC_OTPM)
        return _SHARED


def _strict_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalize_usage(usage: Any) -> Optional[SimpleNamespace]:
    """Anthropic usage -> Gemini öznitelik adları (ledger `_usage_totals`).

    input_tokens cache okuma/yazmayı HARİÇ tutar; prompt_token_count TOPLAM
    girdidir (ledger cache_miss = prompt - cache_read hesaplar).
    Geçersiz/eksik -> None (çağıran tavandan yakar).
    """
    if usage is None:
        return None
    inp = _strict_int(getattr(usage, "input_tokens", None))
    out = _strict_int(getattr(usage, "output_tokens", None))
    if inp is None or out is None:
        return None
    read = getattr(usage, "cache_read_input_tokens", None)
    write = getattr(usage, "cache_creation_input_tokens", None)
    read = 0 if read is None else _strict_int(read)
    write = 0 if write is None else _strict_int(write)
    if read is None or write is None:
        return None
    prompt = inp + read + write
    return SimpleNamespace(
        prompt_token_count=prompt, candidates_token_count=out,
        thoughts_token_count=None,   # düşünme output_tokens'a dahil
        total_token_count=prompt + out,
        cache_read_token_count=read, cache_creation_token_count=write)


_sampling_logged = False


class AnthropicService(AIService):
    def __init__(self, api_key: Optional[str] = None, timeout_s: float = 120.0,
                 transport=None, limiter: Optional[AnthropicRateLimiter] = None):
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport  # test: httpx2.MockTransport (SDK httpx2 kullanır)
        self._limiter = limiter
        self._client = None
        self._client_pid: Optional[int] = None
        self._client_lock = threading.Lock()
        self.collector = None
        self.model_name = None  # bilinçli: default model yok

    @property
    def client(self):
        client = self._client
        if client is not None and self._client_pid == os.getpid():
            return client
        with self._client_lock:
            if self._client is None or self._client_pid != os.getpid():
                key = (self._api_key or os.getenv("ANTHROPIC_API_KEY")
                       or settings.ANTHROPIC_API_KEY)
                if not key:
                    raise AIResponseError(
                        "ANTHROPIC_API_KEY yok — anthropic route'u "
                        "kullanılamaz (fail-closed)")
                import anthropic

                http_client = None
                if self._transport is not None:
                    import httpx2  # anthropic 1.13 httpx değil httpx2 ister

                    http_client = httpx2.Client(transport=self._transport)
                self._client = anthropic.Anthropic(
                    api_key=key, max_retries=0, timeout=self._timeout_s,
                    http_client=http_client)
                self._client_pid = os.getpid()
            return self._client

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(f"Anthropic client kapatma hatasi: {exc}")

    def complete(self, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.7) -> str:
        return self._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=False, response_schema=None, stage="unknown")

    def complete_json(self, prompt: str, max_tokens: int = 6000,
                      temperature: float = 0.3,
                      response_schema: Optional[Dict[str, Any]] = None) -> str:
        return self._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=True, response_schema=response_schema, stage="unknown")

    def _record(self, *, stage, model, usage, finish_reason, latency_ms,
                retry_reason=None, queue_wait_ms=None) -> None:
        collector = getattr(self, "collector", None)
        if collector is None:
            return
        try:
            read = getattr(usage, "cache_read_token_count", None)
            collector.record(
                stage=stage, model=model,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                candidates_tokens=getattr(usage, "candidates_token_count", None),
                thoughts_tokens=None,
                total_tokens=getattr(usage, "total_token_count", None),
                finish_reason=finish_reason, latency_ms=latency_ms,
                retry_reason=retry_reason,
                cache_status=(None if usage is None
                              else ("hit" if read else "miss")),
                provider="anthropic", cache_read_tokens=read,
                cache_creation_tokens=getattr(
                    usage, "cache_creation_token_count", None),
                queue_wait_ms=queue_wait_ms, http_attempts=1)
        except Exception as telemetry_error:  # pragma: no cover
            logger.warning(f"Anthropic telemetri hatasi: {telemetry_error}")

    def _execute(self, prompt: str, *, max_tokens: int,
                 temperature: Optional[float], json_mode: bool,
                 response_schema: Optional[Dict[str, Any]], stage: str,
                 model: Optional[str] = None,
                 thinking_level: Optional[str] = None,
                 thinking_budget: Optional[int] = None,
                 provider: Optional[str] = None) -> str:
        global _sampling_logged

        if provider not in (None, "anthropic"):
            raise AIResponseError(
                f"AnthropicService '{provider}' provider'ını serve edemez")
        if not model:
            raise AIResponseError(
                "Anthropic: model zorunlu (route'tan gelir; varsayılan "
                "model bilinçli olarak YOK)")
        if thinking_budget is not None:
            raise AnthropicContractError(
                "Anthropic invalid argument: thinking_budget Claude 5'te "
                "desteklenmez")
        if temperature is not None and not _sampling_logged:
            _sampling_logged = True
            logger.debug("Anthropic: temperature/top_p gönderilmez (düşürüldü)")

        env = anthropic_request_envelope(
            prompt, model=model, max_tokens=max_tokens, json_mode=json_mode,
            response_schema=response_schema, thinking_level=thinking_level)
        sent_max = env["max_tokens"]
        client = self.client            # anahtar yoksa rezervasyondan ÖNCE patlar
        limiter = self._limiter or _shared_limiter()
        in_bytes = anthropic_request_input_ceiling_bytes(
            prompt, json_mode=json_mode, response_schema=response_schema)

        binding = getattr(self, "cost_binding", None)
        rid = None
        if binding is not None:
            rid = binding.reserve(
                stage=stage, model=model, prompt_bytes=in_bytes,
                max_output_tokens=sent_max, provider="anthropic")

        ticket, wait_ms = limiter.acquire(in_bytes // 3, sent_max)
        started = time.perf_counter()
        try:
            resp = client.messages.create(**env)
        except Exception as exc:
            limiter.release(ticket)
            if binding is not None:   # harcama bilinmiyor: tavandan yak
                binding.charge_ceiling(rid)
            latency = int((time.perf_counter() - started) * 1000)
            err, reason = self._map_error(exc)
            self._record(stage=stage, model=model, usage=None,
                         finish_reason=None, latency_ms=latency,
                         retry_reason=reason[:200], queue_wait_ms=wait_ms)
            if err is None:
                raise
            raise err from exc
        latency = int((time.perf_counter() - started) * 1000)

        usage = _normalize_usage(getattr(resp, "usage", None))
        limiter.release(ticket,
                        getattr(usage, "prompt_token_count", None),
                        getattr(usage, "candidates_token_count", None))
        if binding is not None:
            if usage is None:
                binding.charge_ceiling(rid)
            else:
                binding.settle(rid, model=model, usage=usage)

        stop = getattr(resp, "stop_reason", None)
        text = "".join(
            b.text for b in (getattr(resp, "content", None) or [])
            if getattr(b, "type", None) == "text"
            and isinstance(getattr(b, "text", None), str))
        retry_reason = None
        error: Optional[AIResponseError] = None
        if stop == "refusal":
            retry_reason = "refusal"
            error = AnthropicRefusalError(
                f"Anthropic refusal: stop_reason=refusal (stage={stage})")
        elif not text.strip():
            retry_reason = "empty_content"
            error = AIResponseError(
                f"empty_content: Anthropic boş yanıt (stop_reason={stop})")
        elif usage is None:
            retry_reason = "usage_missing"
        self._record(stage=stage, model=model, usage=usage,
                     finish_reason=stop, latency_ms=latency,
                     retry_reason=retry_reason, queue_wait_ms=wait_ms)
        if error is not None:
            raise error
        return text

    @staticmethod
    def _map_error(exc: Exception):
        """SDK istisnası -> (AIResponseError|None, retry_reason).

        Mesajlar classify_provider_error işaretleriyle eşleşir (429/rate limit/
        503/timeout = bekle-tekrar; 400 bad request/401/403/invalid argument = fatal).
        None = tanınmayan istisna, olduğu gibi yükselt.
        """
        import anthropic

        if isinstance(exc, anthropic.APITimeoutError):
            return AIResponseError(f"Anthropic read timed out: {exc}"), f"timeout: {exc}"
        if isinstance(exc, anthropic.APIConnectionError):
            return (AIResponseError(f"Anthropic connection reset/transport: {exc}"),
                    f"transport: {exc}")
        if isinstance(exc, anthropic.RateLimitError):
            return AIResponseError("Anthropic rate limit (429)"), "rate_limit: 429"
        if isinstance(exc, anthropic.APIStatusError):
            status = exc.status_code
            if status in (401, 403):
                return (AIResponseError(f"Anthropic yetki hatası (api key) {status}"),
                        f"auth: {status}")
            if status >= 500 or status in (408, 409):
                text = (f"service unavailable 503 (status={status})"
                        if status in (503, 529) else f"server disconnected {status}")
                return AIResponseError(f"Anthropic {text}"), f"transport: {status}"
            # diğer 4xx: istek hatalı, tekrar anlamsız -> fatal işaretli metin
            msg = str(getattr(exc, "message", "") or "")[:200]
            return (AIResponseError(
                f"Anthropic {status} bad request (invalid argument): {msg}"),
                f"response_error: {status}")
        return None, f"transport: {type(exc).__name__}: {exc}"
