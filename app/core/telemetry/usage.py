"""Task/istek-scoped, thread-safe AI kullanım toplayıcısı (plan C).

Sözleşme (Codex v5):
- Her provider isteği bir event'tir; retry'lar ayrı event sayılır.
- BOUNDED FLUSH: her FLUSH_EVERY event'te ara yazım — hard-timeout /
  process kill / container restart'ta kayıp en fazla son penceredir.
- Flush, domain session'ından AYRI SessionLocal ile yapılır.
- (request_id, attempt) UNIQUE + ON CONFLICT DO NOTHING → idempotent yazım.
- Başarısız istekler de kaydedilir (token alanları NULL + retry_reason).
- Ham token sayıları kaynak gerçektir; TL/USD hesabı İKİNCİL metriktir ve
  price_snapshot {model, currency, unit_prices, valid_from} ile saklanır.
- Telemetri HİÇBİR koşulda asıl isteği düşürmez (fail-open).
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from loguru import logger

# Fiyat tablosu — kaynak: ai.google.dev/gemini-api/docs/pricing (2026-07).
# Guncelleme gerektiginde valid_from ile yeni satir eklenir; eski event'lerin
# snapshot'i degismez.
PRICE_TABLE: Dict[str, Dict[str, Any]] = {
    "gemini-3.5-flash": {
        "currency": "USD",
        "input_per_m": 1.50,
        "output_per_m": 9.00,
        "valid_from": "2026-05-01",
    },
    # SEO V3 Asama 2 deney kosucusu (plan_seo_v3_test.md v5, 2026-09-07):
    # resmi fiyat sayfasi — input $0.75/M, cikti VE dusunme $3.75/M
    # (dusunme token'lari cikti fiyatindan; CostRates.cost zaten toplar).
    # Gecerlilik 2026-12-31'e kadar; sonrasinda satir yenilenmeli.
    # Motor v3 ve global uretim varsayilani Gemini 3.8 Flash'tir.
    "gemini-3.8-flash": {
        "currency": "USD",
        "input_per_m": 0.75,
        "output_per_m": 3.75,
        "valid_from": "2026-09-07",
        "valid_until": "2026-12-31",
    },
    # Codex v8-6: resmi standart fiyat $0.25 in / $1.50 out (eski 0.20/1.20
    # %20 dusuk gosteriyordu — D benchmark maliyet kapisini carpitirdi)
    "gemini-3.1-flash-lite": {
        "currency": "USD",
        "input_per_m": 0.25,
        "output_per_m": 1.50,
        "valid_from": "2026-07-16",
    },
    # Codex v9-2: embedding maliyeti "tam run" muhasebesine dahil —
    # relevance sifir sayilirsa D'nin <=25 TL kapisi yanlis referansla olculur.
    # Embedding'de cikti token'i yoktur (output_per_m=0).
    "models/gemini-embedding-2": {
        "currency": "USD",
        "input_per_m": 0.20,
        "output_per_m": 0.0,
        "valid_from": "2026-07-16",
    },
    # plan_ai bake-off adayları (2026-07-27 resmi fiyat sayfaları).
    # NOT: DeepSeek'te cache-hit input token'ları çok daha ucuzdur
    # (input_cache_hit_per_m); AiUsageEvent tahmini bunu YOK SAYAR (üst
    # sınır verir) — GERÇEK maliyet screening runner'ında usage'daki
    # hit/miss ayrımıyla hesaplanır (app/core/screening/runner.py).
    "gemini-3.5-flash-lite": {
        "currency": "USD",
        "input_per_m": 0.30,
        "output_per_m": 2.50,
        "valid_from": "2026-07-27",
    },
    "deepseek-v4-flash": {
        "currency": "USD",
        "input_per_m": 0.14,
        "input_cache_hit_per_m": 0.0028,
        "output_per_m": 0.28,
        "valid_from": "2026-07-27",
    },
    "deepseek-v4-pro": {
        "currency": "USD",
        "input_per_m": 0.435,
        "input_cache_hit_per_m": 0.003625,
        "output_per_m": 0.87,
        "valid_from": "2026-07-27",
    },
}


def price_for(model: str) -> Optional[Dict[str, Any]]:
    """TEK FİYAT KAYNAĞI (Codex #4): screening runner ve telemetri aynı
    tablodan okur — ikinci bir sabit listesi tutulmaz."""
    entry = PRICE_TABLE.get(model)
    if entry is None:
        return None
    price = {
        "input": entry["input_per_m"],
        "output": entry["output_per_m"],
        "input_cache_hit": entry.get("input_cache_hit_per_m",
                                     entry["input_per_m"]),
        "currency": entry["currency"],
        "valid_from": entry["valid_from"],
    }
    # Gecmis plan/onay muhurleri bu sozlugun tam seklini baglar. Eski fiyat
    # satirlarina `valid_until: None` eklemek fiyat degismese bile muhru bozar.
    # Alan yalniz fiyat kaynagi gercekten tanimliyorsa tasinir.
    if "valid_until" in entry:
        price["valid_until"] = entry["valid_until"]
    return price

# Hedefler TL cinsinden konusuluyor (orn. "tam run <= 25 TL") ama telemetri
# USD uretir. Karsilastirma yapilirken TARIHLI kur notu SART: D benchmark
# raporu, kosunun yapildigi gunun USD/TRY kuruyla hedefi USD'ye cevirip
# raporda sabitler (kur bu dosyaya gomulmez — bayatlar).

FLUSH_EVERY = 8


def _price_snapshot(model: str) -> Optional[Dict[str, Any]]:
    entry = PRICE_TABLE.get(model)
    if entry is None:
        return None
    return {"model": model, **entry}


def estimate_cost(events: List[dict]) -> Dict[str, Any]:
    """İkincil metrik: event listesinden tahmini maliyet (USD)."""
    total = 0.0
    for e in events:
        snap = e.get("price_snapshot")
        if not snap:
            continue
        inp = (e.get("prompt_tokens") or 0) / 1_000_000 * snap["input_per_m"]
        # Düşünme token'ları çıktı fiyatından faturalanır
        out_tokens = (e.get("candidates_tokens") or 0) + (e.get("thoughts_tokens") or 0)
        out = out_tokens / 1_000_000 * snap["output_per_m"]
        total += inp + out
    return {"estimated_usd": round(total, 4)}


class UsageCollector:
    """Thread-safe kullanım toplayıcısı; bounded-flush ile kalıcı yazım."""

    def __init__(
        self,
        scoring_run_id: Optional[int] = None,
        brand_profile_id: Optional[int] = None,
        task_id: Optional[str] = None,
        flush_every: int = FLUSH_EVERY,
    ):
        self.scoring_run_id = scoring_run_id
        self.brand_profile_id = brand_profile_id
        self.task_id = task_id
        self._flush_every = max(1, flush_every)
        self._lock = threading.Lock()
        self._pending: List[dict] = []
        self._all_events: List[dict] = []  # özet için (yalnız bu process)
        # Mantıksal-çağrı bağlamı (Codex v8-4): thread-local — paralel
        # katmanlar birbirinin retry kapsamını göremez
        self._local = threading.local()

    @contextmanager
    def logical_request(self):
        """Retry döngüsünü TEK mantıksal çağrı olarak bağlar (Codex v8-4).

        Kapsam içindeki tüm record()'lar aynı request_id'yi paylaşır ve
        attempt 1,2,3... artar — retry zinciri telemetride görünür olur.
        Kapsam dışı record() eski davranışta kalır (yeni UUID + attempt=1).
        İç içe kullanım güvenli (önceki bağlam restore edilir).
        """
        prev = getattr(self._local, "ctx", None)
        self._local.ctx = {"request_id": uuid.uuid4().hex, "attempt": 0}
        try:
            yield
        finally:
            self._local.ctx = prev

    def record(
        self,
        *,
        stage: str,
        model: str,
        prompt_tokens: Optional[int] = None,
        candidates_tokens: Optional[int] = None,
        thoughts_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        finish_reason: Optional[str] = None,
        latency_ms: Optional[int] = None,
        retry_reason: Optional[str] = None,
        cache_status: Optional[str] = None,
    ) -> None:
        # Mantıksal-çağrı bağlamı varsa sabit request_id + artan attempt
        ctx = getattr(self._local, "ctx", None)
        if ctx is not None:
            ctx["attempt"] += 1
            request_id, attempt = ctx["request_id"], ctx["attempt"]
        else:
            request_id, attempt = uuid.uuid4().hex, 1
        event = {
            "scoring_run_id": self.scoring_run_id,
            "brand_profile_id": self.brand_profile_id,
            "task_id": self.task_id,
            "request_id": request_id,
            "attempt": attempt,
            "stage": stage[:60],
            "model": model[:100],
            "prompt_tokens": prompt_tokens,
            "candidates_tokens": candidates_tokens,
            "thoughts_tokens": thoughts_tokens,
            "total_tokens": total_tokens,
            "finish_reason": (finish_reason or None) and str(finish_reason)[:40],
            "latency_ms": latency_ms,
            "retry_reason": retry_reason,
            "cache_status": cache_status,
            "price_snapshot": _price_snapshot(model),
        }
        with self._lock:
            self._pending.append(event)
            self._all_events.append(event)
            should_flush = len(self._pending) >= self._flush_every
        if should_flush:
            self.flush()

    def mark_current_attempt_failed(self, reason: str) -> bool:
        """Mark the provider attempt in the active logical request as failed.

        JSON parsing and result-completeness checks happen above ``AIService``.
        By then the provider event may already have been flushed, so update both
        the in-memory event and the persisted row. Existing provider-level
        reasons (transport, blocked response, etc.) are never overwritten.
        """
        ctx = getattr(self._local, "ctx", None)
        if not ctx or ctx.get("attempt", 0) < 1:
            return False

        request_id = ctx["request_id"]
        attempt = ctx["attempt"]
        normalized_reason = str(reason)[:255]
        marked = False

        with self._lock:
            for event in reversed(self._all_events):
                if (
                    event.get("request_id") == request_id
                    and event.get("attempt") == attempt
                ):
                    if not event.get("retry_reason"):
                        event["retry_reason"] = normalized_reason
                        marked = True
                    break

        try:
            from app.database.connection import SessionLocal
            from app.database.models import AiUsageEvent

            db = SessionLocal()
            try:
                updated = (
                    db.query(AiUsageEvent)
                    .filter(
                        AiUsageEvent.request_id == request_id,
                        AiUsageEvent.attempt == attempt,
                        AiUsageEvent.retry_reason.is_(None),
                    )
                    .update(
                        {"retry_reason": normalized_reason},
                        synchronize_session=False,
                    )
                )
                db.commit()
                marked = bool(updated) or marked
            finally:
                db.close()
        except Exception as mark_error:  # pragma: no cover - fail-open
            logger.warning(f"Usage attempt isaretleme basarisiz: {mark_error}")

        return marked

    def flush(self) -> None:
        """Bekleyenleri AYRI session ile yazar; hata isteği düşürmez."""
        with self._lock:
            batch, self._pending = self._pending, []
        if not batch:
            return
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from app.database.connection import SessionLocal
            from app.database.models import AiUsageEvent

            db = SessionLocal()
            try:
                stmt = pg_insert(AiUsageEvent).values(batch).on_conflict_do_nothing(
                    index_elements=["request_id", "attempt"]
                )
                db.execute(stmt)
                db.commit()
            finally:
                db.close()
        except Exception as flush_error:  # pragma: no cover - fail-open
            logger.warning(f"Usage flush başarısız ({len(batch)} event): {flush_error}")
            # Kaybetme: pending'e geri koy (bir sonraki flush dener)
            with self._lock:
                self._pending = batch + self._pending

    def summary(self) -> Dict[str, Any]:
        """TaskResult.result_data özeti — ham toplamlar + ikincil maliyet."""
        with self._lock:
            events = list(self._all_events)
        totals = {
            "requests": len(events),
            "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in events),
            "candidates_tokens": sum(e.get("candidates_tokens") or 0 for e in events),
            "thoughts_tokens": sum(e.get("thoughts_tokens") or 0 for e in events),
            "failed_requests": sum(1 for e in events if e.get("retry_reason")),
        }
        totals.update(estimate_cost(events))
        return totals

    def finalize(self) -> Dict[str, Any]:
        """Kalanları yazar ve özet döndürür (failure yolunda da çağrılır)."""
        self.flush()
        return self.summary()
