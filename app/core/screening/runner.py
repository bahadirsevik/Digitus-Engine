"""Corpus screening runner — batch orkestrasyonu + retry zinciri (plan_ai §6).

GÜVENLİK İLKESİ (plan_ai §2 — terminal eleme YOK):
Tarama BAŞARISIZLIĞI hiçbir koşulda kelimeyi elemez. Çözülemeyen keyword
`unresolved=True` ile işaretlenir ve `UNRESOLVED_FALLBACK_FIT = 1`
(belirsiz/yakın) alır — yani aday birliğinde `fit ∈ {1,2}` kovasına düşer.
Sessiz `fit=0` ataması YASAKTIR: model kaynaklı bir hata, ham-skor kapısının
yerine geçen yeni bir görünmezlik kapısı yaratmamalıdır.

Retry zinciri (her katman ayrı AiUsageEvent üretir):
1. Geçici hata (timeout/rate-limit/transport/boş içerik) → aynı batch,
   `MAX_TRANSIENT_RETRIES` kez, üstel bekleme.
2. Parse/response hatası → aynı batch 1 kez.
3. Eksik ID → yalnız eksik keyword'lerle hedefli retry,
   `MAX_MISSING_RETRIES` tur.
4. Tek-keyword final pass → `SINGLE_RETRY_LIMIT` çağrıyla sınırlı
   (öncelik: girdi sırası).
"""
from __future__ import annotations

import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from app.core.screening.contract import (
    BATCH_SIZE,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    REASON_CODE_VERSION,
    TEMPERATURE,
)
from app.core.screening.providers import (
    CorpusScreeningProvider,
    ScreeningAuthError,
    ScreeningEmptyContentError,
    ScreeningProviderError,
    ScreeningResponseError,
)

UNRESOLVED_FALLBACK_FIT = 1
MAX_TRANSIENT_RETRIES = 2
MAX_PARSE_RETRIES = 1
MAX_MISSING_RETRIES = 2
SINGLE_RETRY_LIMIT = 10
BACKOFF_BASE_S = 1.5

# Fiyat TEK KAYNAKTAN gelir (Codex #4): app/core/telemetry/usage.PRICE_TABLE.
# İkinci bir sabit liste tutulmaz — biri güncellenip diğeri unutulamaz.
from app.core.telemetry.usage import price_for  # noqa: E402

# Girdi tavanı prompt'un GERÇEK boyutundan türetilir: UTF-8 bayt sayısı
# token sayısının matematiksel üst sınırıdır (bir token >= 1 bayt). Prompt
# boyutu bilinmiyorsa bu varsayılan kullanılır (yalnız fallback).
CONSERVATIVE_PROMPT_TOKENS = 4096


class ScreeningBudgetExceeded(RuntimeError):
    """Bütçe/istek rezervasyonu reddedildi — koşu durdurulur."""


@dataclass
class ScreeningBudget:
    """GERÇEK fren (Codex #1): her provider isteğinden ÖNCE rezervasyon.

    Kombinasyon-sonu kontrolü "yumuşak durdurma"dır ve tam bir koşu kadar
    aşım bırakır. Burada istek sınırı TAM uygulanır; maliyette ise sıradaki
    isteğin KONSERVATİF üst maliyeti kalan bütçeyi aşıyorsa istek hiç
    başlamaz.
    """

    model: str
    max_requests: Optional[int] = None
    max_cost_usd: Optional[float] = None
    requests_used: int = 0
    cost_settled: float = 0.0
    cost_reserved: float = 0.0
    ceiling_charges: int = 0
    stopped_reason: Optional[str] = None
    # Paralel koşuda rezervasyon/settle yarışsız olmalı — aksi halde fren
    # aşılabilir (iki thread aynı bakiyeyi görüp ikisi de rezerve eder)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cost_committed(self) -> float:
        """Kesinleşen + halen REZERVE tutulan (uçuştaki istek) toplamı."""
        return self.cost_settled + self.cost_reserved

    def per_request_ceiling(self, prompt_bytes: Optional[int] = None) -> float:
        """Bir isteğin matematiksel üst maliyeti.

        Girdi tarafı prompt'un UTF-8 BAYT sayısından türetilir (token >= 1
        bayt olduğundan bu gerçek bir üst sınırdır); bilinmiyorsa
        CONSERVATIVE_PROMPT_TOKENS fallback'i kullanılır. Çıktı tarafı
        MAX_OUTPUT_TOKENS tavanıdır.
        """
        price = price_for(self.model)
        if not price:
            return 0.0
        input_tokens = (
            prompt_bytes if prompt_bytes is not None
            else CONSERVATIVE_PROMPT_TOKENS
        )
        return (
            input_tokens / 1_000_000 * price["input"]
            + MAX_OUTPUT_TOKENS / 1_000_000 * price["output"]
        )

    def reserve(self, prompt_bytes: Optional[int] = None) -> float:
        """İstekten ÖNCE tavanı GERÇEKTEN ayırır ve rezervasyonu döndürür.

        Ayırma şart (Codex 2. tur #1): yalnız kontrol edip kaydetmemek,
        provider usage döndürmediğinde aynı bütçenin defalarca
        kullanılmasına yol açıyordu. Paralel koşuda kilit altındadır.
        """
        with self._lock:
            return self._reserve_locked(prompt_bytes)

    def _reserve_locked(self, prompt_bytes: Optional[int] = None) -> float:
        if self.max_requests is not None and self.requests_used >= self.max_requests:
            self.stopped_reason = (
                f"request_limit ({self.requests_used}/{self.max_requests})"
            )
            raise ScreeningBudgetExceeded(self.stopped_reason)
        ceiling = self.per_request_ceiling(prompt_bytes)
        if self.max_cost_usd is not None:
            if self.cost_committed + ceiling > self.max_cost_usd:
                self.stopped_reason = (
                    f"cost_limit (bağlanan ${self.cost_committed:.4f} + "
                    f"sonraki istek üst maliyeti ${ceiling:.4f} > "
                    f"${self.max_cost_usd})"
                )
                raise ScreeningBudgetExceeded(self.stopped_reason)
        self.requests_used += 1
        self.cost_reserved += ceiling
        return ceiling

    def settle(self, reservation: float, usage) -> None:
        """Rezervasyonu GERÇEK maliyetle değiştirir.

        Usage eksik/geçersizse (sağlayıcı token döndürmediyse) rezerve
        edilen TAVAN harcanmış kabul edilir — bilinmeyen maliyet sıfır
        sayılmaz.
        """
        with self._lock:
            self._settle_locked(reservation, usage)

    def _settle_locked(self, reservation: float, usage) -> None:
        self.cost_reserved = max(0.0, self.cost_reserved - reservation)
        actual = None
        if usage is not None and getattr(usage, "prompt_tokens", None) is not None:
            actual = compute_cost_usd(self.model, {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "thoughts_tokens": getattr(usage, "thoughts_tokens", 0) or 0,
                "cache_hit_tokens": getattr(usage, "cache_hit_tokens", 0) or 0,
                "cache_miss_tokens": getattr(usage, "cache_miss_tokens", 0) or 0,
            })
        if actual is None:
            self.cost_settled += reservation
            self.ceiling_charges += 1
        else:
            self.cost_settled += actual

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "max_cost_usd": self.max_cost_usd,
            "requests_used": self.requests_used,
            "cost_settled": round(self.cost_settled, 6),
            "cost_reserved_open": round(self.cost_reserved, 6),
            "cost_committed": round(self.cost_committed, 6),
            "ceiling_charged_requests": self.ceiling_charges,
            "default_per_request_ceiling_usd": round(
                self.per_request_ceiling(), 6
            ),
            "stopped_reason": self.stopped_reason,
        }


@dataclass
class ScreeningContext:
    product_definition: str
    content_strategy: str
    social_mode: str
    target_audience: Optional[str] = None

    def as_dict(self) -> Dict[str, str]:
        return {
            "product_definition": self.product_definition,
            "content_strategy": self.content_strategy,
            "social_mode": self.social_mode,
            "target_audience": self.target_audience or "-",
        }


@dataclass
class KeywordScreeningResult:
    keyword_id: int
    keyword: str
    ads_fit: int
    seo_fit: int
    social_fit: int
    reason_codes: Dict[str, str]
    unresolved: bool = False
    unresolved_reason: Optional[str] = None
    membership_violations: List[str] = field(default_factory=list)
    attempt_stage: str = "batch"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "keyword_id": self.keyword_id,
            "keyword": self.keyword,
            "ads_fit": self.ads_fit,
            "seo_fit": self.seo_fit,
            "social_fit": self.social_fit,
            "reason_codes": self.reason_codes,
            "unresolved": self.unresolved,
            "unresolved_reason": self.unresolved_reason,
            "membership_violations": self.membership_violations,
            "attempt_stage": self.attempt_stage,
        }


@dataclass
class ScreeningRunResult:
    results: List[KeywordScreeningResult]
    stats: Dict[str, Any]
    usage: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.as_dict() for r in self.results],
            "stats": self.stats,
            "usage": self.usage,
        }


def permute(keywords: Sequence[Dict[str, Any]], seed: int) -> List[Dict[str, Any]]:
    """Deterministik permütasyon (aynı seed → aynı sıra)."""
    items = list(keywords)
    random.Random(seed).shuffle(items)
    return items


def _batches(items: Sequence[Dict[str, Any]], size: int):
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def compute_cost_usd(model: str, usage_totals: Dict[str, int]) -> Optional[float]:
    """GERÇEK maliyet (provider usage alanlarından; tahmin değil).

    DeepSeek: cache-hit input token'ları ayrı (çok daha ucuz) fiyatlanır.
    Bilinmeyen model → None (sessiz sıfır YAZILMAZ).
    """
    price = price_for(model)
    if not price:
        return None
    hit = usage_totals.get("cache_hit_tokens") or 0
    miss = usage_totals.get("cache_miss_tokens") or 0
    prompt = usage_totals.get("prompt_tokens") or 0
    if hit or miss:
        # Cache bilgisi varsa prompt token'ları hit/miss olarak ayrıştır
        billed_miss = miss
        billed_hit = hit
    else:
        billed_miss, billed_hit = prompt, 0
    out = (usage_totals.get("completion_tokens") or 0) + (
        usage_totals.get("thoughts_tokens") or 0
    )
    hit_rate = price.get("input_cache_hit", price["input"])
    total = (
        billed_miss / 1_000_000 * price["input"]
        + billed_hit / 1_000_000 * hit_rate
        + out / 1_000_000 * price["output"]
    )
    return round(total, 6)


class _UsageAccumulator:
    def __init__(self):
        self.totals = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "thoughts_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "latency_ms_total": 0,
            "failed_attempt_tokens": 0,
        }
        self.failures: Dict[str, int] = {}

    def add(self, usage, *, count_request: bool = True) -> None:
        if count_request:
            self.totals["requests"] += 1
        if usage is None:
            return
        for key, attr in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("thoughts_tokens", "thoughts_tokens"),
            ("cache_hit_tokens", "cache_hit_tokens"),
            ("cache_miss_tokens", "cache_miss_tokens"),
            ("latency_ms_total", "latency_ms"),
        ):
            value = getattr(usage, attr, None)
            if value:
                self.totals[key] += int(value)

    def fail(self, reason_code: str, usage=None) -> None:
        """Başarısız deneme: istek sayılır VE (varsa) token'ları maliyete
        girer — sağlayıcı 200 döndürüp içerik boş bıraktığında ya da çıktı
        parse edilemediğinde token FATURALANIR (Codex #4)."""
        self.totals["requests"] += 1
        self.failures[reason_code] = self.failures.get(reason_code, 0) + 1
        if usage is not None:
            self.add(usage, count_request=False)
            self.totals["failed_attempt_tokens"] += (
                (getattr(usage, "prompt_tokens", 0) or 0)
                + (getattr(usage, "completion_tokens", 0) or 0)
                + (getattr(usage, "thoughts_tokens", 0) or 0)
            )


class SingleRetryBudget:
    """GÖRÜNÜM seviyesinde paylaşılan tekil-retry bütçesi (Codex 11. #1).

    Üretim koşucusu her checkpoint batch'i için `run_screening`'i YENİDEN
    çağırır; yerel sayaç her çağrıda sıfırlandığı için `SINGLE_RETRY_LIMIT`
    fiilen BATCH BAŞINA uygulanıyordu ve preflight'ın fiyatladığı
    "görünüm başına 10 çağrı" sözleşmesi tutmuyordu. Bu nesne bütçeyi
    görünüm (view) genelinde ve thread-safe tutar.
    """

    def __init__(self, limit: Optional[int] = None):
        # Sabit çağrı ANINDA okunur (varsayılan argümana bağlanırsa
        # deney/test ayarları görünmez olurdu)
        self.limit = int(SINGLE_RETRY_LIMIT if limit is None else limit)
        self.used = 0
        self._lock = threading.Lock()

    def try_consume(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(self.limit - self.used, 0)


def run_screening(
    provider: CorpusScreeningProvider,
    context: ScreeningContext,
    keywords: Sequence[Dict[str, Any]],
    *,
    seed: int,
    batch_size: int = BATCH_SIZE,
    collector=None,
    sleep_fn=time.sleep,
    budget: Optional[ScreeningBudget] = None,
    batch_plan: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
    concurrency: int = 1,
    single_retry_budget: Optional["SingleRetryBudget"] = None,
    missing_retry_rounds: Optional[int] = None,
) -> ScreeningRunResult:
    """Tüm evreni tarar; hiçbir keyword sessizce düşmez.

    keywords: [{"id": int, "keyword": str}, ...] — `id` KALICI keyword
    kimliğidir (batch-yerel indeks değil).
    """
    by_id = {int(k["id"]): dict(k) for k in keywords}
    if len(by_id) != len(keywords):
        raise ValueError("keyword id'leri benzersiz olmalı")

    # Collector provider'da yaşar, logical-request gruplaması runner'da:
    # ayrı ayrı verilirlerse retry zinciri telemetride SESSİZCE dağılır
    # (her deneme yeni request_id). Açık geçilmediyse provider'dan devral.
    if collector is None:
        collector = getattr(provider, "collector", None)

    # Batch planı AÇIK verilebilir (konum/komşuluk ablasyon deneyleri için):
    # hangi kelimenin hangi batch'te ve kaçıncı sırada olduğu tam kontrol
    # edilir. Verilmezse üretim davranışı: seed permütasyonu + sıralı kesme.
    if batch_plan is not None:
        planned_ids = [int(k["id"]) for batch in batch_plan for k in batch]
        if sorted(planned_ids) != sorted(by_id):
            raise ValueError(
                "batch_plan evreni tam olarak bir kez kapsamalı "
                f"(plan {len(planned_ids)}, evren {len(by_id)})"
            )
        oversize = [len(b) for b in batch_plan if len(b) > batch_size]
        if oversize:
            raise ValueError(
                f"batch_plan batch_size={batch_size} üst sınırını aşıyor: "
                f"{sorted(oversize)[-3:]} (Codex #2: plan sözleşmesi)"
            )
        main_batches = [list(b) for b in batch_plan]
        ordered = [k for batch in main_batches for k in batch]
        batching_mode = "explicit_plan"
    else:
        ordered = permute(list(by_id.values()), seed)
        main_batches = list(_batches(ordered, batch_size))
        batching_mode = "permutation"
    resolved: Dict[int, KeywordScreeningResult] = {}
    usage_acc = _UsageAccumulator()
    stage_counts = {"batch": 0, "missing_retry": 0, "single_retry": 0}
    violations: Dict[str, Any] = {
        "ghost_ids": 0,
        "duplicate_ids": set(),
        "cross_batch_duplicates": 0,
        "invalid_reason_codes": set(),
        "invalid_reason_code_samples": [],
    }
    parse_retries = 0
    transient_retries = 0
    invalid_item_count = 0
    # Paylaşılan bütçe verilmezse çağrı-yerel bütçe kurulur (tek çağrılık
    # kullanım ve testler için davranış aynıdır)
    single_budget = single_retry_budget or SingleRetryBudget()
    single_calls_used = 0
    # Paralel ana geçişte paylaşılan durumu koruyan TEK kilit (resolved,
    # sayaçlar, ihlal kümeleri, usage toplamı). Provider çağrısının
    # KENDİSİ kilit dışındadır — paralellik oradan gelir.
    state_lock = threading.Lock()

    def _call(batch: List[Dict[str, Any]], stage: str) -> Optional[Any]:
        """Tek batch isteği + geçici-hata/parse retry zinciri.

        TÜM retry zinciri TEK logical_request kapsamındadır: aynı request_id
        + artan attempt (telemetride retry zinciri görünür kalır). Kapsamı
        döngü içine almak her denemeye yeni request_id verirdi.
        """
        nonlocal parse_retries, transient_retries, invalid_item_count
        # Bütçeler hata SINIFINA GÖRE BAĞIMSIZ (Codex #5): parse hatası
        # transient bütçesini tüketemez ve tersi. Aksi halde "1 parse
        # retry" fiilen 3 denemeye çıkıyordu.
        budgets = {"parse": MAX_PARSE_RETRIES, "transient": MAX_TRANSIENT_RETRIES}
        logical = collector.logical_request() if collector is not None else None
        if logical is not None:
            logical.__enter__()
        try:
            while True:
                reservation = 0.0
                try:
                    # GERÇEK fren: rezervasyon İSTEKTEN ÖNCE, gerçek prompt
                    # boyutuyla; settle rezervasyonu gerçekle değiştirir
                    if budget is not None:
                        reservation = budget.reserve(
                            provider.prompt_size_bytes(context.as_dict(), batch)
                        )
                    result = provider.screen_batch(context.as_dict(), batch)
                    if budget is not None:
                        budget.settle(reservation, result.usage)
                except ScreeningAuthError:
                    if budget is not None and reservation:
                        budget.settle(reservation, None)
                    raise
                except ScreeningBudgetExceeded:
                    raise
                except ScreeningProviderError as exc:
                    with state_lock:
                        usage_acc.fail(exc.reason_code,
                                       getattr(exc, "usage", None))
                    if budget is not None:
                        budget.settle(reservation, getattr(exc, "usage", None))
                    cls = getattr(exc, "error_class", "transient")
                    if cls == "fatal" or budgets.get(cls, 0) <= 0:
                        logger.warning(
                            f"screening batch başarısız ({stage}, "
                            f"{exc.reason_code}, sınıf={cls}): {exc}"
                        )
                        return None
                    budgets[cls] -= 1
                    with state_lock:
                        if cls == "parse":
                            parse_retries += 1
                        else:
                            transient_retries += 1
                        used = MAX_TRANSIENT_RETRIES - budgets["transient"]
                        sleep_fn(BACKOFF_BASE_S * used)
                    continue

                with state_lock:
                    usage_acc.add(result.usage)
                    invalid_item_count += len(result.invalid_items)
                return result
        finally:
            if logical is not None:
                logical.__exit__(None, None, None)

    def _absorb(result, batch: List[Dict[str, Any]], stage: str) -> None:
        """Sözleşme ihlalleri RESOLVED SAYILMAZ (Codex #3).

        `exactly-once` çıktı sözleşmesi: her istenen id TAM BİR KEZ ve
        geçerli reason-code'larla dönmeli. İhlaller (hayalet id, aynı
        yanıtta tekrar eden id, enum-dışı reason code) sayılır ve etkilenen
        id çözülmemiş bırakılır → hedefli retry'a gider; retry'lar da
        çözemezse unresolved fallback devreye girer (eleme YOK).
        """
        if result is None:
            return
        with state_lock:
            _absorb_locked(result, batch, stage)

    def _absorb_locked(result, batch: List[Dict[str, Any]], stage: str) -> None:
        allowed = {int(k["id"]) for k in batch}
        seen_in_response: Dict[int, int] = {}
        for item in result.items:
            seen_in_response[item.id] = seen_in_response.get(item.id, 0) + 1

        for item in result.items:
            if item.id not in allowed:
                violations["ghost_ids"] += 1
                continue
            if seen_in_response[item.id] > 1:
                # Çelişkili olabilir — "ilk kazanır" sessiz kabulü YASAK
                violations["duplicate_ids"].add(item.id)
                continue
            if item.id in resolved:
                violations["cross_batch_duplicates"] += 1
                continue
            member_violations = result.membership_violations.get(item.id, [])
            if member_violations:
                violations["invalid_reason_codes"].add(item.id)
                violations["invalid_reason_code_samples"].extend(
                    member_violations[:2]
                )
                continue  # geçerli kabul EDİLMEZ → retry
            kw = by_id[item.id]
            resolved[item.id] = KeywordScreeningResult(
                keyword_id=item.id,
                keyword=kw["keyword"],
                ads_fit=item.ads_fit,
                seo_fit=item.seo_fit,
                social_fit=item.social_fit,
                reason_codes=item.reason_codes.model_dump(),
                attempt_stage=stage,
            )
            stage_counts[stage] += 1

    budget_stopped: Optional[str] = None
    try:
        # 1) Ana geçiş — batch'ler bağımsızdır, paralel koşulabilir.
        # Sonuçların İÇERİĞİ sıradan bağımsızdır (ID'ler ayrık, ilk-yazan
        # kazanır kuralı ID başına tek yazıcı demektir).
        if concurrency > 1 and len(main_batches) > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [(b, pool.submit(_call, b, "batch"))
                           for b in main_batches]
                for b, fut in futures:
                    _absorb(fut.result(), b, "batch")
        else:
            for batch in main_batches:
                _absorb(_call(batch, "batch"), batch, "batch")

        # 2) Eksik-ID hedefli retry turları
        # Tekil retry fazında (tek kelimelik batch) eksik-ID turu ANLAMSIZ:
        # aynı çağrı tekrarlanır ve maliyet tavanı sessizce büyürdü
        # (Codex 12. tur #1 fazı) — çağıran 0 geçebilir.
        rounds = (MAX_MISSING_RETRIES if missing_retry_rounds is None
                  else int(missing_retry_rounds))
        for _ in range(rounds):
            missing = [k for k in ordered if int(k["id"]) not in resolved]
            if not missing:
                break
            for batch in _batches(missing, batch_size):
                _absorb(_call(batch, "missing_retry"), batch, "missing_retry")

        # 3) Tek-keyword final pass (sınırlı)
        missing = [k for k in ordered if int(k["id"]) not in resolved]
        for kw in missing:
            # Bütçe GÖRÜNÜM genelinde paylaşılır: aynı görünümün diğer
            # batch'lerinin tükettiği kota burada da geçerlidir
            if not single_budget.try_consume():
                break
            single_calls_used += 1
            _absorb(_call([kw], "single_retry"), [kw], "single_retry")
    except ScreeningBudgetExceeded as exc:
        # Bütçe bitti: koşu TEMİZ durur, kalan kelimeler unresolved olur —
        # sessiz eleme yine YOK. CLI bunu görüp tüm bake-off'u durdurur.
        budget_stopped = str(exc)
        logger.warning(f"screening bütçe freni: {exc}")

    # 4) Çözülemeyenler — ELENMEZ, belirsiz kovasına düşer (plan_ai §2)
    unresolved_ids = [int(k["id"]) for k in ordered if int(k["id"]) not in resolved]
    for kid in unresolved_ids:
        kw = by_id[kid]
        resolved[kid] = KeywordScreeningResult(
            keyword_id=kid,
            keyword=kw["keyword"],
            ads_fit=UNRESOLVED_FALLBACK_FIT,
            seo_fit=UNRESOLVED_FALLBACK_FIT,
            social_fit=UNRESOLVED_FALLBACK_FIT,
            reason_codes={"ads": "AMBIGUOUS", "seo": "AMBIGUOUS",
                          "social": "AMBIGUOUS"},
            unresolved=True,
            unresolved_reason="screening_unresolved",
            attempt_stage="unresolved",
        )

    results = [resolved[int(k["id"])] for k in keywords]
    usage_totals = dict(usage_acc.totals)
    cost = compute_cost_usd(provider.model, usage_totals)
    fit_dist = {
        ch: {str(v): 0 for v in (0, 1, 2)}
        for ch in ("ads", "seo", "social")
    }
    for r in results:
        fit_dist["ads"][str(r.ads_fit)] += 1
        fit_dist["seo"][str(r.seo_fit)] += 1
        fit_dist["social"][str(r.social_fit)] += 1

    coverage = (
        round((len(results) - len(unresolved_ids)) / len(results), 4)
        if results else None
    )
    stats = {
        "universe": len(results),
        "batch_size": batch_size,
        "batching_mode": batching_mode,
        "concurrency": concurrency,
        "expected_batches": math.ceil(len(results) / batch_size) if results else 0,
        "resolved_by_stage": stage_counts,
        "unresolved": len(unresolved_ids),
        "unresolved_ids": unresolved_ids[:50],
        "coverage": coverage,
        "parse_retries": parse_retries,
        "transient_retries": transient_retries,
        "single_retry_calls": single_calls_used,
        "invalid_items": invalid_item_count,
        # Sözleşme ihlalleri (Codex #3) — hepsi retry'a yol açar, sessiz
        # kabul yok. Kalıcı ihlal unresolved'a düşer (yine eleme YOK).
        "contract_violations": {
            "ghost_ids": violations["ghost_ids"],
            "duplicate_ids": len(violations["duplicate_ids"]),
            "cross_batch_duplicates": violations["cross_batch_duplicates"],
            "invalid_reason_codes": len(violations["invalid_reason_codes"]),
            "invalid_reason_code_samples": sorted(
                set(violations["invalid_reason_code_samples"])
            )[:10],
        },
        "fit_distribution": fit_dist,
        "unresolved_fallback_fit": UNRESOLVED_FALLBACK_FIT,
        "budget_stopped": budget_stopped,
        "budget": budget.snapshot() if budget is not None else None,
        "prompt_version": PROMPT_VERSION,
        "reason_code_version": REASON_CODE_VERSION,
        "temperature": TEMPERATURE,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "seed": seed,
    }
    usage = {
        **usage_totals,
        "failures_by_reason": dict(usage_acc.failures),
        "cost_usd": cost,
        "cost_source": (
            "provider_usage" if cost is not None else "unknown_model_price"
        ),
    }
    return ScreeningRunResult(results=results, stats=stats, usage=usage)
