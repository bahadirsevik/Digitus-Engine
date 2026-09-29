"""
Google Gemini, OpenAI ve Anthropic API'leri için birleşik wrapper.
"""
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

import httpx
from google import genai
from google.genai import types as genai_types
from loguru import logger

from app.config import settings

# Plan G: transport hatası imzaları — httpx istisnaları normalde
# isinstance ile yakalanır; SDK bazı hataları RuntimeError'a sarıp
# stringleştirdiği için mesaj imzaları da gerekir (run-18: 4×
# "Cannot send a request, as the client has been closed").
TRANSPORT_ERROR_SIGNATURES = (
    "client has been closed",
    "connection reset",
    "connection refused",
    "connection aborted",
    "server disconnected",
    "remote protocol error",
    "read timed out",
    "connect timeout",
)

JSON_ONLY_SUFFIX = (
    "\n\nSADECE geçerli JSON formatında yanıt ver, "
    "başka hiçbir şey yazma."
)


def build_gemini_contents(prompt: str, *, json_mode: bool) -> str:
    """Provider'a gönderilen nihai metni tek kaynaktan üretir."""
    return f"{prompt}{JSON_ONLY_SUFFIX}" if json_mode else prompt


def gemini_request_input_ceiling_bytes(
    prompt: str,
    *,
    json_mode: bool,
    response_schema: Optional[Dict[str, Any]] = None,
) -> int:
    """Gemini girdisinin konservatif, tokenizer-bağımsız üst sınırı.

    Bütçe rezervasyonu ham prompt'a değil provider'a gönderilen nihai request
    zarfına dayanır. Canonical JSON zarfı; JSON talimatını, MIME türünü,
    response schema'yı ve alan adlarını içerir. Her token en az bir UTF-8
    baytından türediği için zarfın bayt boyutu faturalandırılabilir kullanıcı
    girdisi için konservatif token üst sınırıdır.

    Şema serialize edilemiyorsa çağrıdan önce fail-closed olur.
    """
    envelope: Dict[str, Any] = {
        "contents": build_gemini_contents(prompt, json_mode=json_mode),
    }
    if json_mode:
        envelope["response_mime_type"] = "application/json"
        if response_schema is not None:
            envelope["response_schema"] = response_schema
    payload = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return len(payload.encode("utf-8"))


@dataclass(frozen=True)
class GroundedCompletion:
    """Grounded provider yanıtı ve denetlenebilir arama kanıtı."""

    text: str
    search_queries: List[str]
    evidence_urls: List[str]
    supports: List[Dict[str, Any]] = field(default_factory=list)


def is_transport_error(exc: BaseException) -> bool:
    """Taşıma katmanı hatası mı? (retriable + 'transport-error' etiketi)"""
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return True
    message = str(exc).lower()
    return any(sig in message for sig in TRANSPORT_ERROR_SIGNATURES)


class AIResponseError(RuntimeError):
    """Gemini yaniti guvenlik/blok/bos-candidate nedeniyle alinamadi.

    `response.text` SAFETY/RECITATION gibi finish_reason'larda None doner (google-genai)
    ya da ValueError firlatir (eski google-generativeai); _extract_text ikisini de
    yakalayip bu tek, acik istisnaya cevirir. Cagiranlar (intent fallback, icerik bulk
    donguleri) tek problemli keyword yuzunden tum batch'i dusurmek yerine o birimi atlayabilir.
    """


class StageScopedAIService:
    """Immutable, stage-bağlı AI servis sarmalayıcısı (plan C).

    Paralel katmanlar aynı root servisi paylaşır; stage/model/thinking
    bilgisi bu FROZEN sarmalayıcıda taşınır — mutable `service.stage`
    yarışı tasarımla imkânsız. Sarmalayıcı client'ın SAHİBİ DEĞİLDİR,
    kapatamaz (client yaşam döngüsü yalnız root serviste).
    """

    __slots__ = ("_root", "stage", "model", "thinking_level",
                 "thinking_budget", "provider")

    def __init__(self, root, stage: str, model: Optional[str] = None,
                 thinking_level: Optional[str] = None,
                 thinking_budget: Optional[int] = None,
                 provider: Optional[str] = None):
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "thinking_level", thinking_level)
        object.__setattr__(self, "thinking_budget", thinking_budget)
        object.__setattr__(self, "provider", provider)

    def __setattr__(self, name, value):  # immutability guard
        raise AttributeError("StageScopedAIService immutable'dır")

    def for_stage(self, stage: str, **overrides):
        return self._root.for_stage(stage, **overrides)

    def complete(self, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.7) -> str:
        return self._root._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=False, response_schema=None,
            stage=self.stage, model=self.model,
            thinking_level=self.thinking_level,
            thinking_budget=self.thinking_budget,
            provider=self.provider,
        )

    def complete_json(self, prompt: str, max_tokens: int = 6000,
                      temperature: float = 0.3,
                      response_schema: Optional[Dict[str, Any]] = None) -> str:
        return self._root._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=True, response_schema=response_schema,
            stage=self.stage, model=self.model,
            thinking_level=self.thinking_level,
            thinking_budget=self.thinking_budget,
            provider=self.provider,
        )

    def complete_grounded(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.2,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> GroundedCompletion:
        """Google Search Grounding kullanan izole çağrı.

        `complete_json` sözleşmesini ve mevcut provider çağrılarını değiştirmez.
        Schema verilmesi güncel modelde tek-çağrı optimizasyonudur; çağıran iki
        adımlı fallback'i ayrıca yönetir.
        """
        return self._root._execute_grounded(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_schema=response_schema,
            stage=self.stage,
            model=self.model,
            thinking_level=self.thinking_level,
            provider=self.provider,
        )


class AIService(ABC):
    """Abstract base class for AI services."""

    def for_stage(self, stage: str, *, model: Optional[str] = None,
                  thinking_level: Optional[str] = None,
                  thinking_budget: Optional[int] = None,
                  provider: Optional[str] = None,
                  ) -> "StageScopedAIService":
        """Stage-scoped immutable wrapper döndürür (plan C).

        Bilinmeyen stage çağrıyı DÜŞÜRMEZ: default model kullanılır,
        telemetride unknown_stage uyarısı üretilir. `thinking_budget`
        OPT-IN'dir: verilmezse üretim davranışı aynen (yalnız level).
        `provider` yalnız RoutedAIService kökünde anlamlıdır (benchmark
        RoutePinnedAI); üretim yolu geçmez.
        """
        from app.core.constants import AI_STAGES
        if stage not in AI_STAGES:
            logger.warning(f"unknown_stage: '{stage}' registry'de yok — default model")
        return StageScopedAIService(self, stage=stage, model=model,
                                    thinking_level=thinking_level,
                                    thinking_budget=thinking_budget,
                                    provider=provider)
    
    @abstractmethod
    def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.7
    ) -> str:
        """Generate a completion for the given prompt."""
        pass

    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 6000,
        temperature: float = 0.3,
        response_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate a JSON completion for the given prompt.

        max_tokens bir TAVANDIR (yalniz kullanilan token faturalanir) —
        dusunme token'lari da ayni butceden dustugu icin cimri tavanlar
        kesinti + retry firtinasi uretir (run-16 dersi); genis tut.
        """
        pass


class GeminiService(AIService):
    """Google Gemini API implementation."""
    
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = settings.GEMINI_MODEL
        self._client: Optional[genai.Client] = None
        self._client_pid: Optional[int] = None
        self._client_lock = threading.Lock()
        # Usage telemetri collector'i (plan C) — task/istek baslangicinda atanir;
        # thread-safe'tir, paralel katmanlar guvenle paylasir
        self.collector = None

    @property
    def client(self) -> genai.Client:
        """Lazy client — double-checked lock + PID kontrolu (plan G).

        Lazy'nin nedeni: eksik/bos api_key'de DI/instantiation anini degil,
        ilk cagriyi patlatmak (cagiranlardaki try/except fallback zincirleri
        devrede kalir).

        Kilidin nedeni (run-18: 4x "Cannot send a request, as the client has
        been closed"): paralel katmanlar ayni root servisi paylasir; kilitsiz
        lazy-init'te iki thread iki client yaratir, yariste kaybeden client
        referanssiz kalip finalize edilirken uzerinde in-flight istek olabilir.

        PID kontrolunun nedeni: Celery prefork'ta parent process'te yaratilmis
        bir client fork sonrasi child'a tasinirsa socket'leri paylasilir/bozuk
        olur — "task icinde yaratiliyor" varsayimina guvenilmez. Fork sonrasi
        eski client KAPATILMAZ (parent'in fd'lerini kapatmak parent'i bozar),
        yalnizca referans birakilir.
        """
        client = self._client
        if client is not None and self._client_pid == os.getpid():
            return client
        with self._client_lock:
            if self._client is None or self._client_pid != os.getpid():
                self._client = genai.Client(api_key=self._api_key)
                self._client_pid = os.getpid()
            return self._client

    def close(self) -> None:
        """Client'i kapatir — YALNIZ root servis sahibi cagirir (plan G).

        StageScopedAIService sarmalayicilarinda close yoktur (tasarim geregi).
        Idempotent ve fail-open: kapatma hatasi akisi dusurmez. Cagri yerleri:
        get_ai dependency finally'si (istek-scope) ve Celery task finally'leri
        (task-scope) — is bittikten sonra, in-flight istek kalmamisken.
        """
        with self._client_lock:
            client, self._client, self._client_pid = self._client, None, None
        if client is None:
            return
        try:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()
        except Exception as close_error:  # pragma: no cover - fail-open
            logger.warning(f"Gemini client kapatma hatasi: {close_error}")

    @staticmethod
    def _thinking_config() -> Optional[genai_types.ThinkingConfig]:
        """Dusunme seviyesi kontrolu (maliyet+kesinti optimizasyonu).

        API default'u medium: run-16 olcumunde dusunme token'lari cikti
        butcesinin %70-95'ini yiyip yaniti MAX_TOKENS'ta kesiyordu ve $9/M
        cikti fiyatindan faturalaniyordu. GEMINI_THINKING_LEVEL bos ise
        config gonderilmez (API default'una donus icin acil kapi).
        """
        level = (settings.GEMINI_THINKING_LEVEL or "").strip().lower()
        if not level:
            return None
        return genai_types.ThinkingConfig(thinking_level=level)

    @staticmethod
    def _extract_text(response) -> str:
        """Gemini yanitindan metni guvenli sekilde cikarir.

        `response.text` SAFETY/RECITATION finish_reason'larinda veya candidate
        yoklugunda ValueError firlatir; bunu acik bir AIResponseError'a cevirir.
        MAX_TOKENS gibi kismi yanitlar (text erisilebilir) oldugu gibi donulur.
        """
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None)
        if block_reason:
            raise AIResponseError(f"Gemini istemi engellendi (block_reason={block_reason})")

        candidates = getattr(response, "candidates", None)
        if not candidates:
            raise AIResponseError("Gemini yanit dondurmedi (candidate yok)")

        finish_reason = getattr(candidates[0], "finish_reason", None)
        try:
            text = response.text
        except Exception as exc:  # ValueError: blocked/no-parts
            raise AIResponseError(
                f"Gemini yaniti alinamadi (finish_reason={finish_reason}): {exc}"
            ) from exc

        # Non-STOP finish_reason gözlemlenebilirliği (sözleşme DEĞİŞMEZ —
        # MAX_TOKENS kesik metin yine döner, sadece artık görünür olur).
        # Amaç: "Unterminated string ~char 220" kesilmelerinin kök nedenini
        # (düşünme token'ları max_output_tokens'ı yiyor hipotezi) kanıtlamak.
        finish_value = getattr(finish_reason, "value", finish_reason)
        if finish_value not in (None, 1, "STOP"):  # google-generativeai: 1; google-genai: "STOP"
            usage = getattr(response, "usage_metadata", None)
            logger.warning(
                "Gemini non-STOP finish_reason={} text_len={} prompt_tokens={} "
                "candidates_tokens={} total_tokens={} thoughts_tokens={}",
                finish_reason,
                len(text or ""),
                getattr(usage, "prompt_token_count", None),
                getattr(usage, "candidates_token_count", None),
                getattr(usage, "total_token_count", None),
                getattr(usage, "thoughts_token_count", None),
            )

        if not text or not text.strip():
            raise AIResponseError(
                f"Gemini bos yanit dondurdu (finish_reason={finish_reason})"
            )
        return text

    def _execute(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: Optional[float],
        json_mode: bool,
        response_schema: Optional[Dict[str, Any]],
        stage: str,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> str:
        """Tek provider isteği (plan C): stage-bilinçli model seçimi +
        telemetri kaydı. Retry'lar ÇAĞIRANDA yaşar — her istek ayrı event.

        `thinking_budget` verilirse ThinkingConfig'e SAĞLAYICI-ZORLAMALI
        düşünme token tavanı olarak eklenir (benchmark hard-cap sözleşmesi;
        üretim yolu bunu geçmez).
        """
        import time as _time

        from app.core.constants import AI_STAGES

        if provider not in (None, "gemini"):
            raise AIResponseError(
                f"GeminiService '{provider}' provider'ını SERVE EDEMEZ — "
                f"routing RoutedAIService kökünde yapılır (fail-closed)")

        effective_model = (
            model
            or (settings.AI_STAGE_MODELS or {}).get(stage)
            or self.model_name
        )

        contents = build_gemini_contents(prompt, json_mode=json_mode)
        config_kwargs: Dict[str, Any] = {"max_output_tokens": max_tokens}
        # Geriye uyumlu: temperature=None -> alan config'e HIC KONMAZ.
        # Gemini 3.8 sampling parametrelerini (temperature/top_p/top_k/
        # candidate_count) kabul etmez; SEO V3 kosucusu None gonderir.
        # Varsayilan yollar (0.3 / 0.7 / 0.2) BAYT AYNI kalir.
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
            if response_schema is not None:
                config_kwargs["response_schema"] = response_schema

        level = (thinking_level or settings.GEMINI_THINKING_LEVEL or "").strip().lower()
        if level or thinking_budget is not None:
            # API kısıtı (spike-kanıtlı, gemini_thinking_spike.json P2/P3):
            # "You can only set only one of thinking budget and thinking
            # level" — budget verilirse level GÖNDERİLMEZ.
            tc_kwargs: Dict[str, Any] = {}
            if thinking_budget is not None:
                tc_kwargs["thinking_budget"] = int(thinking_budget)
            elif level:
                tc_kwargs["thinking_level"] = level
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                **tc_kwargs
            )

        # Downstream ledger (plan §10.2): rezervasyon birimi GERCEK HTTP
        # denemesidir. Bagli degilse (off kosulari) davranis DEGISMEZ.
        binding = getattr(self, "cost_binding", None)
        reservation_id = None
        if binding is not None:
            reservation_id = binding.reserve(
                stage=stage, model=effective_model,
                prompt_bytes=len(prompt.encode("utf-8")),
                max_output_tokens=int(max_tokens or 0))

        started = _time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=effective_model,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            # Token faturalanmis OLABILIR: bilinmeyen harcama TAVANDAN yakilir
            if binding is not None:
                binding.charge_ceiling(reservation_id)
            # Başarısız istek de kaydedilir — tokenlar NULL (plan C).
            # Taşıma katmanı hataları "transport-error" etiketi alır (plan G):
            # SQL'de client-closed/connection sınıfı ayrı sayılabilir olmalı.
            reason_prefix = "transport-error: " if is_transport_error(exc) else ""
            self._record_usage(
                stage=stage, model=effective_model,
                usage=None, finish_reason=None,
                latency_ms=int((_time.perf_counter() - started) * 1000),
                retry_reason=f"{reason_prefix}{exc}"[:200],
                unknown_stage=stage not in AI_STAGES,
            )
            raise

        usage = getattr(response, "usage_metadata", None)
        finish_reason = getattr(
            getattr(response, "candidates", [None])[0]
            if getattr(response, "candidates", None) else None,
            "finish_reason", None,
        )
        latency_ms = int((_time.perf_counter() - started) * 1000)

        # Codex v8-4: kayıt _extract_text SONRASINDA — blocked/boş yanıt
        # "başarı" olarak yazılmaz; token bilgileri KORUNARAK
        # retry_reason='response_error: ...' ile kaydedilir
        try:
            text = self._extract_text(response)
        except Exception as exc:
            self._record_usage(
                stage=stage, model=effective_model,
                usage=usage, finish_reason=finish_reason,
                latency_ms=latency_ms,
                retry_reason=f"response_error: {exc}"[:200],
                unknown_stage=stage not in AI_STAGES,
            )
            # Bloklanmis/bos yanitta da TOKENLAR FATURALANIR
            if binding is not None:
                binding.settle(reservation_id, model=effective_model,
                               usage=usage)
            raise

        self._record_usage(
            stage=stage, model=effective_model,
            usage=usage, finish_reason=finish_reason,
            latency_ms=latency_ms,
            retry_reason=None,
            unknown_stage=stage not in AI_STAGES,
        )
        if binding is not None:
            binding.settle(reservation_id, model=effective_model, usage=usage)
        return text

    def _execute_grounded(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        response_schema: Optional[Dict[str, Any]],
        stage: str,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> GroundedCompletion:
        """Tek Google Search Grounding çağrısı; mevcut `_execute`ten izole."""
        if provider not in (None, "gemini"):
            raise AIResponseError(
                f"GROUNDING_UNSUPPORTED_PROVIDER: {provider!r}")
        import time as _time

        from app.core.constants import AI_STAGES

        effective_model = model or settings.COMPETITOR_DISCOVERY_MODEL
        config_kwargs: Dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "tools": [genai_types.Tool(google_search=genai_types.GoogleSearch())],
        }
        if temperature is not None:      # None -> alan gonderilmez (bkz. _execute)
            config_kwargs["temperature"] = temperature
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
        level = (thinking_level or settings.GEMINI_THINKING_LEVEL or "").strip().lower()
        if level:
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_level=level
            )

        started = _time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            self._record_usage(
                stage=stage,
                model=effective_model,
                usage=None,
                finish_reason=None,
                latency_ms=int((_time.perf_counter() - started) * 1000),
                retry_reason=("transport-error: " if is_transport_error(exc) else "")
                + str(exc)[:180],
                unknown_stage=stage not in AI_STAGES,
            )
            raise

        usage = getattr(response, "usage_metadata", None)
        candidate = (
            response.candidates[0]
            if getattr(response, "candidates", None)
            else None
        )
        finish_reason = getattr(candidate, "finish_reason", None)
        latency_ms = int((_time.perf_counter() - started) * 1000)
        try:
            text = self._extract_text(response)
        except Exception as exc:
            self._record_usage(
                stage=stage,
                model=effective_model,
                usage=usage,
                finish_reason=finish_reason,
                latency_ms=latency_ms,
                retry_reason=f"response_error: {exc}"[:200],
                unknown_stage=stage not in AI_STAGES,
            )
            raise

        metadata = getattr(candidate, "grounding_metadata", None)
        queries = [
            str(value).strip()
            for value in (getattr(metadata, "web_search_queries", None) or [])
            if str(value).strip()
        ]
        urls: List[str] = []
        chunks = list(getattr(metadata, "grounding_chunks", None) or [])
        chunk_urls: List[str] = []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            uri = str(getattr(web, "uri", "") or "").strip()
            chunk_urls.append(uri)
            if uri and uri not in urls:
                urls.append(uri)

        supports: List[Dict[str, Any]] = []
        for support in getattr(metadata, "grounding_supports", None) or []:
            segment = getattr(support, "segment", None)
            segment_text = str(getattr(segment, "text", "") or "").strip()
            support_urls: List[str] = []
            for index in getattr(support, "grounding_chunk_indices", None) or []:
                if isinstance(index, int) and 0 <= index < len(chunk_urls):
                    uri = chunk_urls[index]
                    if uri and uri not in support_urls:
                        support_urls.append(uri)
            if segment_text:
                supports.append({"text": segment_text, "evidence_urls": support_urls})

        self._record_usage(
            stage=stage,
            model=effective_model,
            usage=usage,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            retry_reason=None,
            unknown_stage=stage not in AI_STAGES,
        )
        return GroundedCompletion(
            text=text,
            search_queries=queries,
            evidence_urls=urls,
            supports=supports,
        )

    def _record_usage(self, *, stage, model, usage, finish_reason,
                      latency_ms, retry_reason, unknown_stage) -> None:
        """Collector varsa event kaydeder; telemetri asla isteği düşürmez."""
        collector = getattr(self, "collector", None)
        if collector is None:
            return
        try:
            collector.record(
                stage=("unknown:" + stage) if unknown_stage else stage,
                model=model,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                candidates_tokens=getattr(usage, "candidates_token_count", None),
                thoughts_tokens=getattr(usage, "thoughts_token_count", None),
                total_tokens=getattr(usage, "total_token_count", None),
                finish_reason=str(getattr(finish_reason, "value", finish_reason))
                if finish_reason is not None else None,
                latency_ms=latency_ms,
                retry_reason=retry_reason,
            )
        except Exception as telemetry_error:  # pragma: no cover
            logger.warning(f"Usage telemetri kaydı başarısız: {telemetry_error}")

    def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.7
    ) -> str:
        return self._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=False, response_schema=None, stage="unknown",
        )

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 6000,
        temperature: float = 0.3,
        response_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        return self._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=True, response_schema=response_schema, stage="unknown",
        )


def _usage_token_count(value: Any, field: str) -> Optional[int]:
    """DeepSeek usage alanı sözleşmesi: None | nonnegative int (Codex).

    Sağlayıcı "100" (string), negatif ya da kesirli değer döndürürse
    collector'a/maliyet özetine sızmaz — None'a normalize edilir; bütçe
    tarafında "usage unavailable" ceiling-charge yoluna düşer. İçerik
    kararı KAYBEDİLMEZ (bool da int sayılmaz — geçerli sayaç değildir).
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning(
            f"DeepSeek usage.{field} geçersiz ({value!r}) — None sayıldı "
            f"(ceiling-charge yolu)")
        return None
    return value


def _deepseek_thinking_on(thinking_level) -> bool:
    """`thinking_level` -> DeepSeek bool thinking.

    None/boş/kapatma sinyali -> KAPALI (varsayılan, üretim davranışı).
    Herhangi bir seviye ("low", "medium", "high") -> AÇIK. DeepSeek
    gövdesi seviye kabul etmez; seviye YALNIZ aç/kapa olarak yorumlanır
    ve bu artifact'a açıkça yazılır (Gemini'deki seviye ile aynı şey
    DEĞİLDİR).
    """
    if not thinking_level:
        return False
    return str(thinking_level).strip().lower() not in (
        "disabled", "off", "none", "false")


def build_deepseek_request_payload(prompt: str, *, model: str,
                                   max_tokens: int, temperature: float,
                                   json_mode: bool,
                                   thinking: bool = False) -> Dict[str, Any]:
    """DeepSeek /chat/completions gövdesi — TEK KAYNAK (Codex Katman-A):
    hem `DeepSeekService._execute` hem bütçe rezervasyon zarfı BU
    üreticiden geçer; zarf ile gerçek istek birbirinden sapamaz.
    """
    contents = build_gemini_contents(prompt, json_mode=json_mode)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": contents}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Spike P4 kanıtı: parametre düşerse varsayılan THINKING —
        # her çağrıda AÇIKÇA gönderilir, düşürülemez. `thinking=True`
        # YALNIZ açık opt-in ile gelir (benchmark/deney yolu); üretim ve
        # screening çağrıları varsayılanı kullanır ve BAYT AYNI kalır.
        "thinking": {"type": "enabled" if thinking else "disabled"},
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        # response_schema BİLEREK gönderilmez: yerel doğrulama zinciri
    return payload


def deepseek_request_input_ceiling_bytes(prompt: str, *, json_mode: bool,
                                         model: str, max_tokens: int,
                                         temperature: float,
                                         thinking: bool = False) -> int:
    """GERÇEK DeepSeek request gövdesinin wire-bayt sayısı.

    Serileştirme httpx 0.28 encode_json ile BİREBİR aynı (kompakt
    ayraçlar + ensure_ascii=False + allow_nan=False) — wire-byte
    eşitliği testle kilitli. Girdi token'ları yalnız messages
    içeriğinden türediği için tam gövde baytı token sayısının üst
    sınırıdır; Gemini zarfından farkı: şema gönderilmez, gövde
    OpenAI-uyumludur (Codex: provider-aware ceiling).
    """
    payload = build_deepseek_request_payload(
        prompt, model=model, max_tokens=max_tokens,
        temperature=temperature, json_mode=json_mode, thinking=thinking)
    return len(json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def _deepseek_cost_fields(usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """DeepSeek usage'ından ATOMİK maliyet alanları (Codex 4. tur).

    İki kural:
    - Kısmi usage gerçek maliyet DEĞİLDİR: prompt_tokens VEYA
      completion_tokens geçersiz/eksikse DÖRT alan da None döner —
      tüketici tarafında event "usage unavailable" sayılır ve bütçe
      ceiling-charge yakar. Alan-bazlı None (önceki davranış) kısmi
      maliyeti gerçek maliyet gibi gösterip rezervasyon tavanını
      söndürüyordu.
    - Spike kanıtı (v4pro_capability_spike): completion_tokens
      reasoning'i İÇERİR — çift sayım olmasın diye
      candidates = completion - reasoning, thoughts = reasoning.
      reasoning alanı var ama geçersizse ya da reasoning > completion
      ise bölüşüm bilinemez → atomik unavailable.
    """
    unavailable: Dict[str, Optional[int]] = {
        "prompt_tokens": None, "candidates_tokens": None,
        "thoughts_tokens": None, "total_tokens": None}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    pt = _usage_token_count(usage.get("prompt_tokens"), "prompt_tokens")
    ct = _usage_token_count(usage.get("completion_tokens"),
                            "completion_tokens")
    if pt is None or ct is None:
        if usage:  # hata yollarının boş dict'i için gürültü basma
            logger.warning(
                "DeepSeek usage kısmi/geçersiz (prompt/completion "
                "güvenilmez) — ATOMİK unavailable, ceiling-charge yolu")
        return unavailable
    rt_raw = details.get("reasoning_tokens")
    rt = _usage_token_count(rt_raw, "reasoning_tokens")
    if rt_raw is not None and rt is None:
        logger.warning(
            "DeepSeek reasoning_tokens geçersiz — completion bölüşümü "
            "bilinemez, usage ATOMİK unavailable")
        return unavailable
    thoughts = rt or 0
    if thoughts > ct:
        logger.warning(
            f"DeepSeek reasoning ({thoughts}) > completion ({ct}) — "
            f"usage ATOMİK unavailable (ceiling-charge yolu)")
        return unavailable
    return {
        "prompt_tokens": pt,
        "candidates_tokens": ct - thoughts,
        "thoughts_tokens": thoughts,
        "total_tokens": _usage_token_count(usage.get("total_tokens"),
                                           "total_tokens"),
    }


class DeepSeekService(AIService):
    """DeepSeek (OpenAI-uyumlu) kanal adapter'ı (plan_v4pro 4.1).

    Spike-kanıtlı sözleşmeler (benchmark/v4pro_capability_spike.json):
    - `thinking: {type: disabled}` HER ÇAĞRIDA AÇIKÇA gönderilir —
      parametre düşerse sağlayıcı varsayılanı THINKING'dir ve token
      bütçesini yer (P4 kanıtı).
    - JSON mode `response_format`; `response_schema` GÖNDERİLMEZ — çıktı,
      katmanların mevcut yerel parse/doğrulama zincirinden geçer.
    - Model adı route'tan gelir; burada varsayılan model YOKTUR
      (model adından provider tahmini yasağının simetriği).
    Telemetri GeminiService ile aynı collector sözleşmesini kullanır;
    DeepSeek completion_tokens reasoning'i İÇERDİĞİ için bölüşüm
    candidates = completion - reasoning, thoughts = reasoning'dir
    (çift sayım yasağı) ve kısmi/geçersiz usage ATOMİK unavailable
    gider (bkz. _deepseek_cost_fields).
    """

    BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, timeout_s: float = 120.0,
                 transport=None):
        self._api_key = api_key
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport  # test: httpx.MockTransport
        self._client = None
        self._client_pid: Optional[int] = None
        self._client_lock = threading.Lock()
        self.collector = None
        self.model_name = None  # bilinçli: default model yok

    @property
    def client(self):
        import httpx

        client = self._client
        if client is not None and self._client_pid == os.getpid():
            return client
        with self._client_lock:
            if self._client is None or self._client_pid != os.getpid():
                key = self._api_key or os.getenv("DEEPSEEK_API_KEY")
                if not key:
                    raise AIResponseError(
                        "DEEPSEEK_API_KEY yok — DeepSeek route'u "
                        "kullanılamaz (fail-closed)")
                kwargs = dict(
                    base_url=self._base_url, timeout=self._timeout_s,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"})
                if self._transport is not None:
                    kwargs["transport"] = self._transport
                self._client = httpx.Client(**kwargs)
                self._client_pid = os.getpid()
            return self._client

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(f"DeepSeek client kapatma hatasi: {exc}")

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
            json_mode=True, response_schema=response_schema,
            stage="unknown")

    def _record(self, *, stage, model, usage: dict, finish_reason,
                latency_ms, retry_reason=None) -> None:
        collector = getattr(self, "collector", None)
        if collector is None:
            return
        try:
            # Codex 4. tur: maliyet alanları ATOMİK — kısmi geçersiz
            # usage'da dördü birden None gider (ceiling-charge yolu);
            # cache_status maliyet alanı değil, bağımsız teşhis kalır
            cost_fields = _deepseek_cost_fields(usage)
            hit = _usage_token_count(
                usage.get("prompt_cache_hit_tokens"),
                "prompt_cache_hit_tokens")
            cache_status = None
            if hit is not None:
                cache_status = "hit" if hit else "miss"
            collector.record(
                stage=stage, model=model,
                finish_reason=finish_reason,
                latency_ms=latency_ms,
                retry_reason=retry_reason,
                cache_status=cache_status,
                **cost_fields,
            )
        except Exception as telemetry_error:  # pragma: no cover
            logger.warning(f"DeepSeek telemetri hatasi: {telemetry_error}")

    def _execute(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        response_schema: Optional[Dict[str, Any]],
        stage: str,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> str:
        import time as _time

        import httpx

        if provider not in (None, "deepseek"):
            raise AIResponseError(
                f"DeepSeekService '{provider}' provider'ını serve edemez")
        if not model:
            raise AIResponseError(
                "DeepSeek: model zorunlu (route'tan gelir; varsayılan "
                "model bilinçli olarak YOK)")

        # `thinking_level` DeepSeek'te BOOL'a indirgenir: seviye verilmiş
        # ve kapatma sinyali değilse thinking AÇIK. Varsayılan (None) eski
        # davranıştır — üretim ve screening yolları DEĞİŞMEZ.
        thinking_on = _deepseek_thinking_on(thinking_level)
        # TEK KAYNAK: rezervasyon zarfı da aynı üreticiden hesaplanır
        payload = build_deepseek_request_payload(
            prompt, model=model, max_tokens=max_tokens,
            temperature=temperature, json_mode=json_mode,
            thinking=thinking_on)

        started = _time.perf_counter()
        try:
            resp = self.client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None,
                         latency_ms=int((_time.perf_counter() - started)
                                        * 1000),
                         retry_reason=f"timeout: {exc}"[:200])
            raise AIResponseError(
                f"DeepSeek read timed out: {exc}") from exc
        except httpx.TransportError as exc:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None,
                         latency_ms=int((_time.perf_counter() - started)
                                        * 1000),
                         retry_reason=f"transport: {exc}"[:200])
            raise AIResponseError(
                f"DeepSeek connection reset/transport: {exc}") from exc

        latency_ms = int((_time.perf_counter() - started) * 1000)
        if resp.status_code in (401, 403):
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason=f"auth: {resp.status_code}")
            raise AIResponseError(
                f"DeepSeek yetki hatası (api key) {resp.status_code}")
        if resp.status_code == 429:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason="rate_limit: 429")
            raise AIResponseError("DeepSeek rate limit (429)")
        if resp.status_code >= 500:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason=f"transport: {resp.status_code}")
            raise AIResponseError(
                f"DeepSeek server disconnected {resp.status_code}")
        if resp.status_code >= 400:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason=f"response_error: {resp.status_code}")
            raise AIResponseError(
                f"DeepSeek {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason=f"response_error: {exc}"[:200])
            raise AIResponseError(
                f"DeepSeek gövdesi JSON değil: {exc}") from exc

        # Codex: 200 dönse de gövde şekli bozuk olabilir (liste gövde,
        # string choice, bozuk message) — AttributeError yerine tipli hata
        # + usage-korumalı telemetri
        if not isinstance(body, dict):
            self._record(stage=stage, model=model, usage={},
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason="response_error: malformed body")
            raise AIResponseError(
                f"DeepSeek gövdesi dict değil: {type(body).__name__}")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        choices = body.get("choices")
        first = (choices[0] if isinstance(choices, list) and choices
                 else None)
        message = (first.get("message") if isinstance(first, dict)
                   else None)
        if not isinstance(message, dict):
            self._record(stage=stage, model=model, usage=usage,
                         finish_reason=None, latency_ms=latency_ms,
                         retry_reason="response_error: malformed choices")
            raise AIResponseError(
                "DeepSeek yanıt şekli bozuk (choices/message)")
        finish_reason = first.get("finish_reason")
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        # normalize: string "7" gibi bir değer hem yanlış warning üretir
        # hem collector'a tip bozuk sızardı
        reasoning = _usage_token_count(
            details.get("reasoning_tokens"), "reasoning_tokens") or 0
        if reasoning and not thinking_on:
            logger.warning(
                f"DeepSeek: thinking disabled'a rağmen {reasoning} "
                f"reasoning token — sözleşme sinyali, maliyet kayıtta")

        content = message.get("content")
        if not content or not str(content).strip():
            self._record(stage=stage, model=model, usage=usage,
                         finish_reason=finish_reason,
                         latency_ms=latency_ms,
                         retry_reason="empty_content")
            raise AIResponseError(
                f"DeepSeek boş yanıt döndürdü "
                f"(finish_reason={finish_reason})")

        self._record(stage=stage, model=model, usage=usage,
                     finish_reason=finish_reason, latency_ms=latency_ms)
        return str(content)


VALID_PROVIDERS = ("gemini", "deepseek")


class RoutedAIService(AIService):
    """Provider-yönlendirmeli kök servis (plan_v4pro 4.1).

    - `AI_STAGE_ROUTES` boşsa HER ŞEY varsayılan backend'e gider (bugünkü
      üretim davranışı birebir; Gemini ya da testlerde Mock).
    - Tipli route: {"stage": {"provider": "deepseek", "model": "..."}} —
      bilinmeyen provider veya modelsiz route FAIL-CLOSED.
    - `provider` çağrı override'ı (RoutePinnedAI) route tablosunun önüne
      geçer; model zorunludur. Model adından provider TAHMİN EDİLMEZ.
    - Telemetri: `collector` ataması tüm backend'lere yayılır — hangi
      provider olursa olsun aynı collector'a akar.
    """

    def __init__(self, default_backend: AIService,
                 deepseek_backend: Optional[AIService] = None):
        self._default = default_backend
        self._deepseek = deepseek_backend
        self._collector = getattr(default_backend, "collector", None)
        self._cost_binding = getattr(default_backend, "cost_binding", None)
        # Codex: kilitsiz check-then-create paralel batch'te birden fazla
        # backend/aktif client üretebilir — double-check init kilidi
        self._ds_lock = threading.Lock()

    # collector: katmanlar `root.collector = c` yapar — yayılmalı
    @property
    def collector(self):
        return self._collector

    @collector.setter
    def collector(self, value):
        self._collector = value
        self._default.collector = value
        if self._deepseek is not None:
            self._deepseek.collector = value

    # Codex 22. tur #1: muhasebe bagi da collector GIBI TUM backend'lere
    # yayilmali. Yalniz kok nesneye yazmak, gercek cagriyi yapan
    # `_default`i BAGLAMADAN birakiyordu ("bagli" diyen ama rezervasyon
    # acmayan sessiz hata).
    @property
    def cost_binding(self):
        return self._cost_binding

    @cost_binding.setter
    def cost_binding(self, value):
        self._cost_binding = value
        self._default.cost_binding = value
        if self._deepseek is not None:
            self._deepseek.cost_binding = value

    def _deepseek_backend(self) -> AIService:
        backend = self._deepseek
        if backend is not None:
            return backend
        with self._ds_lock:
            if self._deepseek is None:
                backend = DeepSeekService()
                backend.collector = self._collector
                # Lazy dogan backend de muhasebeye BAGLI dogar
                backend.cost_binding = self._cost_binding
                self._deepseek = backend
            return self._deepseek

    def _resolve(self, stage: str, model: Optional[str],
                 provider: Optional[str]):
        if provider is not None:
            if provider not in VALID_PROVIDERS:
                raise AIResponseError(
                    f"bilinmeyen provider: {provider!r} (izinli: "
                    f"{VALID_PROVIDERS}) — model adından tahmin YAPILMAZ")
            if not model:
                raise AIResponseError(
                    f"provider override'ı ({provider}) açık model ister")
            return provider, model
        routes = settings.AI_STAGE_ROUTES or {}
        if routes:
            # Codex: 'intnet' gibi yazım hatalı stage anahtarı sessizce
            # yok sayılamaz — tablo her çözümde bütün olarak doğrulanır
            from app.core.constants import AI_STAGES

            unknown = [k for k in routes if k not in AI_STAGES]
            if unknown:
                raise AIResponseError(
                    f"AI_STAGE_ROUTES bilinmeyen stage anahtarları: "
                    f"{unknown} — yazım hatası olabilir (fail-closed)")
        if stage in routes:
            # Codex: `if route:` boş dict'i legacy'ye düşürüyordu — açık
            # ama boş/yanlış-tip route YAPILANDIRMA HATASIDIR
            route = routes[stage]
            if not isinstance(route, dict) or not route:
                raise AIResponseError(
                    f"AI_STAGE_ROUTES[{stage}] boş/geçersiz: {route!r} — "
                    f"provider+model zorunlu (fail-closed)")
            rp = route.get("provider")
            rm = model or route.get("model")
            if rp not in VALID_PROVIDERS:
                raise AIResponseError(
                    f"AI_STAGE_ROUTES[{stage}].provider geçersiz: {rp!r}")
            if not rm:
                raise AIResponseError(
                    f"AI_STAGE_ROUTES[{stage}].model zorunlu")
            return rp, rm
        # Legacy yol: varsayılan backend (model çözümü backend'in kendi
        # AI_STAGE_MODELS zinciri) — davranış birebir eski hali
        return "gemini", model

    def _backend(self, provider: str) -> AIService:
        if provider == "deepseek":
            return self._deepseek_backend()
        return self._default

    def _execute(self, prompt, *, max_tokens, temperature, json_mode,
                 response_schema, stage, model=None, thinking_level=None,
                 thinking_budget=None, provider=None):
        rp, rm = self._resolve(stage, model, provider)
        backend = self._backend(rp)
        return backend._execute(
            prompt, max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode, response_schema=response_schema,
            stage=stage, model=rm, thinking_level=thinking_level,
            thinking_budget=thinking_budget)

    def _execute_grounded(self, prompt, **kwargs):
        """Grounded çağrı da ROUTE ÇÖZER (Codex): deepseek'e route edilmiş
        bir stage'in grounded çağrısı sessizce Gemini'ye düşmez — tipli
        hata üretir; ayar yok sayılmaz."""
        stage = kwargs.get("stage", "unknown")
        rp, rm = self._resolve(stage, kwargs.get("model"),
                               kwargs.pop("provider", None))
        if rp != "gemini":
            raise AIResponseError(
                f"GROUNDING_UNSUPPORTED_PROVIDER: grounding yalnız Gemini "
                f"backend'inde var; stage '{stage}' route'u {rp!r} — "
                f"grounded akışları için route'u düzeltin (fail-closed)")
        kwargs["model"] = rm
        return self._default._execute_grounded(prompt, **kwargs)

    def complete(self, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.7) -> str:
        return self._default.complete(prompt, max_tokens, temperature)

    def complete_json(self, prompt: str, max_tokens: int = 6000,
                      temperature: float = 0.3,
                      response_schema: Optional[Dict[str, Any]] = None) -> str:
        return self._default.complete_json(prompt, max_tokens, temperature,
                                           response_schema)

    def close(self) -> None:
        try:
            closer = getattr(self._default, "close", None)
            if callable(closer):
                closer()
        finally:
            if self._deepseek is not None:
                self._deepseek.close()

    def __getattr__(self, name):
        # Geriye uyumluluk: model_name, client vb. varsayılan backend'den
        return getattr(self._default, name)


class MockAIService(AIService):
    """
    Mock AI service for testing without API calls.
    Returns deterministic responses.
    """
    
    def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.7
    ) -> str:
        return "This is a mock response for testing purposes."

    def _execute(self, prompt, *, max_tokens, temperature, json_mode,
                 response_schema, stage, model=None, thinking_level=None,
                 thinking_budget=None, provider=None):
        if json_mode:
            return self.complete_json(prompt, max_tokens, temperature, response_schema)
        return self.complete(prompt, max_tokens, temperature)

    def _execute_grounded(self, prompt, *, max_tokens, temperature,
                          response_schema, stage, model=None,
                          thinking_level=None,
                          provider=None) -> GroundedCompletion:
        """Anahtarsız ortamda grounding YOK gibi davranır: boş metadata döner,
        çağıran (competitor discovery) iki-adımlı fallback'ten geçip temiz
        '0 aday' ile tamamlanır — AttributeError ile DISCOVERY_FAILED olmaz."""
        return GroundedCompletion(
            text='{"candidates": []}', search_queries=[], evidence_urls=[]
        )

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 6000,
        temperature: float = 0.3,
        response_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        # Return mock intent analysis
        import json
        import re
        import random
        
        if "intent" in prompt.lower() or "niyet" in prompt.lower():
            # Extract keyword IDs from prompt
            # Pattern matches "- {id}: {keyword}" format
            ids = re.findall(r'- (\d+):', prompt)
            
            results = []
            intents = ['transactional', 'informational', 'commercial', 'navigational', 'trend_worthy']
            
            for kid in ids:
                results.append({
                    "keyword_id": int(kid),
                    "intent_type": random.choice(intents),
                    "confidence": 0.85,
                    "reasoning": "Mock AI analysis based on keyword pattern."
                })
                
            return json.dumps(results)
            
        return json.dumps({"result": "mock"})


def scoped(ai_service, stage: str, **overrides):
    """ai_service'i stage'e baglar; for_stage'i olmayan test fake'lerini
    oldugu gibi gecirir (plan C — tum katman kurucularinda kullanilir)."""
    if hasattr(ai_service, "for_stage"):
        return ai_service.for_stage(stage, **overrides)
    return ai_service


def logical_request(ai_service):
    """Ayni-prompt retry dongusunu TEK mantiksal cagri olarak telemetriye
    baglar (Codex v8-4): kapsam icindeki provider denemeleri sabit request_id
    + artan attempt ile kaydedilir. Collector'siz servis / test fake'i icin
    no-op context doner — cagiran kosulsuz `with` kullanabilir."""
    from contextlib import nullcontext

    root = getattr(ai_service, "_root", ai_service)
    collector = getattr(root, "collector", None)
    if collector is None or not hasattr(collector, "logical_request"):
        return nullcontext()
    return collector.logical_request()


def mark_attempt_failed(ai_service, reason: str) -> bool:
    """Annotate the latest provider event in the active logical request.

    Parsers call this after a syntactically valid provider response proves
    unusable. Services without telemetry (including test fakes) remain no-op.
    """
    root = getattr(ai_service, "_root", ai_service)
    collector = getattr(root, "collector", None)
    if collector is None or not hasattr(collector, "mark_current_attempt_failed"):
        return False
    return collector.mark_current_attempt_failed(reason)


def get_ai_service(use_mock: bool = False, api_key: Optional[str] = None) -> AIService:
    """
    Konfigürasyona göre doğru AI servisini döner.
    
    Args:
        use_mock: Test için mock servis kullan
        api_key: Opsiyonel API anahtarı (verilmezse env'den okunur)
    
    Returns:
        AI service instance
    """
    if use_mock:
        # Codex: mock modda deepseek route'u lazy GERÇEK DeepSeekService
        # yaratabiliyordu (anahtar varsa ücretli çağrı riski) — dışa
        # çağrısız garanti için İKİ backend de mock olmalı
        return RoutedAIService(MockAIService(),
                               deepseek_backend=MockAIService())

    # Varsayılan backend Gemini; RoutedAIService AI_STAGE_ROUTES boşken
    # birebir eski davranıştır (her çağrı varsayılan backend'e düşer)
    return RoutedAIService(GeminiService(api_key=api_key))
