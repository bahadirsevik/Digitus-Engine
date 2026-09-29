# -*- coding: utf-8 -*-
"""Gemini downstream çağrılarının kalıcı ledger'a bağlanması (plan §10.2).

Neden gerekli: bugüne kadar YALNIZ screening (`BUDGET_SCREENING`) rezerve
ediliyordu; kanal atamasının Gemini çağrıları (intent, marka filtresi,
ADS/SOCIAL prefilter, transfer, expansion, SEO metadata) hiçbir bütçeye
bağlı DEĞİLDİ. Bu yüzden kullanıcıya "birleşik hard-cap" vaat edilemiyordu
(Codex 14. tur #1). Burada bağlanır ve `combined` cap GERÇEK garanti olur.

Sözleşme (screening tarafıyla AYNI disiplin):
- Rezervasyon birimi GERÇEK HTTP denemesidir: `_execute` her çağrıldığında
  ayrı rezervasyon açılır (retry'lar dahil).
- Tavan matematikseldir: prompt UTF-8 baytı (token >= 1 bayt) + istenen
  `max_output_tokens`, modelin GÜNCEL fiyatıyla.
- Usage yoksa/eksikse TAVAN YAKILIR (sessizce "bedava" sayılmaz).
- Cap aşılacaksa `BudgetExceeded` çağrıdan ÖNCE yükselir: para harcanmaz,
  ilgili katman kendi fallback semantiğini uygular.
- Ledger BAĞLI DEĞİLSE (off koşuları) davranış BUGÜNKÜYLE AYNIDIR.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from app.core.telemetry.ai_cost_budget import BUDGET_DOWNSTREAM, AiCostLedger
from app.core.telemetry.usage import price_for

class LedgerBindError(RuntimeError):
    """Muhasebe kurulamadı — çağrı YAPILMAZ (fail-closed)."""


class UnpricedModelError(RuntimeError):
    """Model fiyat tablosunda yok: tavan hesaplanamaz, harcama YAPILMAZ.

    Codex 22. tur #2: sessizce muhasebesiz devam etmek "birleşik hard-cap"
    vaadini bozardı.
    """


class DownstreamLedgerBinding:
    """AI servisine takılan ince muhasebe sarmalayıcısı."""

    def __init__(self, ledger: AiCostLedger, *, scoring_run_id: int,
                 attempt_id: int):
        self.ledger = ledger
        self.scoring_run_id = int(scoring_run_id)
        self.attempt_id = int(attempt_id)
        self._lock = threading.Lock()
        self._calls = 0
        self.ceiling_charges = 0
        self.settled_usd = 0.0

    def next_request_id(self, stage: str) -> str:
        """`dsn:{attempt}:{stage}:c{n}` — paralel katmanlarda da benzersiz."""
        with self._lock:
            self._calls += 1
            index = self._calls
        return f"dsn:{self.attempt_id}:{stage or 'unknown'}:c{index}"

    def request_ceiling_usd(self, model: str, prompt_bytes: int,
                            max_output_tokens: int) -> float:
        price = price_for(model)
        if not price:
            raise UnpricedModelError(
                f"'{model}' fiyat tablosunda YOK — tavan hesaplanamaz, "
                f"muhasebesiz çağrı YAPILMAZ (fail-closed)")
        return (float(prompt_bytes) / 1_000_000 * price["input"]
                + float(max_output_tokens) / 1_000_000 * price["output"])

    def reserve(self, *, stage: str, model: str, prompt_bytes: int,
                max_output_tokens: int) -> int:
        """Çağrıdan ÖNCE rezervasyon; başarısızsa çağrı YAPILMAZ."""
        ceiling = self.request_ceiling_usd(model, prompt_bytes,
                                           max_output_tokens)
        if ceiling <= 0:
            raise UnpricedModelError(
                f"'{model}' için hesaplanan tavan {ceiling} — geçersiz")
        return self.ledger.reserve(
            kind=BUDGET_DOWNSTREAM,
            request_id=self.next_request_id(stage),
            ceiling_usd=ceiling, auto_attempt=True, stage=stage,
            provider="gemini", model=model)

    def settle(self, reservation_id: Optional[int], *, model: str,
               usage) -> None:
        """Gerçek usage ile kapatır; token kanıtı yoksa TAVAN yakılır."""
        if reservation_id is None:
            return
        from app.core.screening.runner import compute_cost_usd

        totals = _usage_totals(usage)
        if totals is None:
            # Codex 22. tur #3: KISMİ/BOZUK usage düşük maliyet yazamaz —
            # prompt VE completion alanları geçerli değilse TAVAN yakılır
            self.ledger.charge_ceiling(reservation_id)
            with self._lock:
                self.ceiling_charges += 1
            return
        cost = compute_cost_usd(model, totals)
        if cost is None:
            self.ledger.charge_ceiling(reservation_id)
            with self._lock:
                self.ceiling_charges += 1
            return
        self.ledger.settle(reservation_id, cost)
        with self._lock:
            self.settled_usd += float(cost)

    def charge_ceiling(self, reservation_id: Optional[int]) -> None:
        if reservation_id is None:
            return
        self.ledger.charge_ceiling(reservation_id)
        with self._lock:
            self.ceiling_charges += 1

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {"downstream_calls": self._calls,
                    "downstream_settled_usd": round(self.settled_usd, 6),
                    "downstream_ceiling_charges": self.ceiling_charges}


def _strict_int(value, *, allow_missing: bool = False) -> Optional[int]:
    """Token alanı SIKI doğrulanır (Codex 22. tur #3).

    bool/str/negatif/kesirli değerler GEÇERSİZDİR; sessizce 0 sayılmaz.
    `allow_missing=True` alanlar (thoughts) yoksa 0 kabul edilir.
    """
    if value is None:
        return 0 if allow_missing else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return int(value)


def _usage_totals(usage) -> Optional[Dict[str, int]]:
    """Gemini `usage_metadata` → ortak token sözlüğü; geçersizse None.

    ZORUNLU alanlar: prompt + candidates. Biri eksik/bozuksa çağrının
    maliyeti BİLİNMİYOR demektir ve tavandan yakılır (düşük maliyet
    yazmak yerine).
    """
    if usage is None:
        return None
    prompt = _strict_int(getattr(usage, "prompt_token_count", None))
    completion = _strict_int(getattr(usage, "candidates_token_count", None))
    thoughts = _strict_int(getattr(usage, "thoughts_token_count", None),
                           allow_missing=True)
    if prompt is None or completion is None or thoughts is None:
        return None
    if prompt <= 0 and completion <= 0:
        return None            # token kanıtı YOK
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "thoughts_tokens": thoughts}


# Kanal atamasinin AI asamalari. Ledger yalniz Gemini `_execute` yolunu
# muhasebelestirir; bu asamalardan biri baska saglayiciya route edilirse
# muhasebesiz harcama olur (Codex 23. tur #1).
ASSIGNMENT_STAGES = ("intent", "brand_filter", "ads_prefilter",
                     "social_prefilter", "seo_metadata")


class DownstreamRouteError(RuntimeError):
    """Atama asamalarindan biri Gemini DISINA route edilmis."""


def assignment_routes_are_ledgered(settings=None) -> list:
    """Ledger'a BAGLANAMAYAN route'lari dondurur (bos = guvenli)."""
    from app.config import settings as default_settings

    settings = settings or default_settings
    routes = getattr(settings, "AI_STAGE_ROUTES", None) or {}
    bad = []
    for stage in ASSIGNMENT_STAGES:
        entry = routes.get(stage)
        if not entry:
            continue
        provider = (entry.get("provider") if isinstance(entry, dict)
                    else getattr(entry, "provider", None))
        if provider and str(provider).lower() != "gemini":
            bad.append(f"{stage}->{provider}")
    return bad


def require_ledgered_routes(settings=None) -> None:
    """Muhasebesiz harcama riski varsa TIPLI hata (fail-closed)."""
    bad = assignment_routes_are_ledgered(settings)
    if bad:
        raise DownstreamRouteError(
            f"atama asamalari Gemini disina route edilmis: {bad} — bu "
            f"cagrilar ledger'a BAGLANAMAZ, birlesik cap garantisi bozulur")


def _executing_backends(ai_service) -> list:
    """GERÇEKTEN provider çağrısı yapan nesneler.

    `RoutedAIService` gibi yönlendiriciler kendileri HTTP yapmaz; asıl
    çağrıyı `_default`/`_deepseek` backend'leri yapar. Bağlamayı yalnız
    köke yazmak, "bağlandı" diyen ama rezervasyon AÇMAYAN sessiz bir
    hataydı (Codex 22. tur #1).
    """
    found = []
    seen = set()

    def _walk(obj, depth=0):
        if obj is None or depth > 2 or id(obj) in seen:
            return
        seen.add(id(obj))
        if hasattr(obj, "_execute") or hasattr(obj, "_execute_grounded"):
            found.append(obj)
        for name in ("_default", "_deepseek", "_gemini", "_backend",
                     "primary", "inner"):
            _walk(getattr(obj, name, None), depth + 1)

    _walk(ai_service)
    return found


def attach_downstream_ledger(ai_service, ledger: AiCostLedger, *,
                             scoring_run_id: int, attempt_id: int
                             ) -> DownstreamLedgerBinding:
    """Bağlamayı takar ve GERÇEKTEN bağlandığını DOĞRULAR (fail-closed)."""
    if ai_service is None or ledger is None:
        raise LedgerBindError("ai_service/ledger yok — muhasebe kurulamaz")
    binding = DownstreamLedgerBinding(
        ledger, scoring_run_id=scoring_run_id, attempt_id=attempt_id)
    # Kök nesne (yönlendirici) setter'ı varsa yayılımı KENDİSİ yapar;
    # yazma engellenirse SESSİZ geçilmez — aşağıdaki doğrulama yakalar
    try:
        setattr(ai_service, "cost_binding", binding)
    except Exception as exc:  # noqa: BLE001
        raise LedgerBindError(
            f"{type(ai_service).__name__} muhasebe bağı yazılamadı: {exc}"
        ) from exc
    backends = _executing_backends(ai_service)
    if not backends:
        raise LedgerBindError(
            f"{type(ai_service).__name__} içinde provider çağrısı yapan "
            f"backend bulunamadı — muhasebesiz çağrı riskine izin verilmez")
    unbound = []
    for backend in backends:
        if getattr(backend, "cost_binding", None) is not binding:
            try:
                setattr(backend, "cost_binding", binding)
            except Exception as exc:  # noqa: BLE001
                unbound.append(f"{type(backend).__name__}: {exc}")
                continue
        if getattr(backend, "cost_binding", None) is not binding:
            unbound.append(type(backend).__name__)
    if unbound:
        raise LedgerBindError(
            f"muhasebe şu backend'lere BAĞLANAMADI: {unbound} — çağrı "
            f"yapılmaz (fail-closed)")
    return binding


__all__ = ["ASSIGNMENT_STAGES", "DownstreamLedgerBinding",
           "DownstreamRouteError", "LedgerBindError", "UnpricedModelError",
           "assignment_routes_are_ledgered", "attach_downstream_ledger",
           "require_ledgered_routes"]
