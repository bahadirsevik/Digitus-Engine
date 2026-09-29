"""Gemini referans karakterizasyonu — test edilebilir çekirdek (plan_v4pro §6.1).

Codex bulgusu: mevcut koşucu tek koşu yapar, çıktının üzerine yazar, canlı
workspace bağlamını okur, tekrar kimliği/giriş hash'i/hard-cap taşımaz. Bu
modül o sözleşmeleri kurar; CLI ince kalır:

- `planned_requests`     : aşama başına KESİN batch sayısı (retry hariç —
                           alt sınır; tavan retry çarpanıyla ayrı verilir).
- `stage_unit_costs`     : geçmiş telemetriden istek başına USD dağılımı
                           (TAHMİN; fail-closed — telemetri yoksa uydurmaz).
- `cost_plan`            : beklenen maliyet + retry payı ile TAVAN.
- `freeze_context`       : ürün tanımı + marka profil bağlamı TEK SEFER
                           çözülür ve SHA ile mühürlenir (canlı sürüklenme
                           yok); kaynak run/workspace kimliği kayda girer.
- `fixture_fingerprint`  : madde kimlik+kelime + şema hash'i.
- `repeat_flip_report`   : tekrarlar arası karar değişimi (eksen bazlı).
- `derived_allowances`   : §6.3 mekanik kuralı — aday kapı payları REFERANS
                           dağılımından, aday sonucu görülmeden türetilir.

Üzerine yazma koruması `app.core.screening.holdout.guard_no_overwrite` ile
paylaşılır (aynı sözleşme: mevcut kanıt silinemez).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence

from app.core.constants import (
    BRAND_FILTER_BATCH_SIZE,
    INTENT_BATCH_SIZE,
    PREFILTER_BATCH_SIZE,
    SEO_METADATA_BATCH_SIZE,
)

STAGES = ("intent", "ads_prefilter", "social_prefilter",
          "brand_filter", "seo_metadata")
STAGE_BATCH = {
    "intent": INTENT_BATCH_SIZE,
    "ads_prefilter": PREFILTER_BATCH_SIZE,
    "social_prefilter": PREFILTER_BATCH_SIZE,
    "brand_filter": BRAND_FILTER_BATCH_SIZE,
    "seo_metadata": SEO_METADATA_BATCH_SIZE,
}
# Üretim sıcaklıkları — canlı koddan doğrulandı (intent_analyzer 0.3,
# prefilter default 0.3, brand_filter._evaluate_batch 0.2, metadata 0.3)
STAGE_TEMPERATURE = {
    "intent": 0.3, "ads_prefilter": 0.3, "social_prefilter": 0.3,
    "brand_filter": 0.2, "seo_metadata": 0.3,
}
RETRY_CEILING_FACTOR = 1.3   # tavan payı: parse/eksik-ID retry'ları için
FLIP_MARGIN = 0.05           # §6.3: izin = referans maks + 0.05

FLIP_AXES = ["intent_type", "gt", "ga", "ads_class",
             "opinion_discussion", "curiosity_comparison", "agenda_theme"]


class CharacterizationError(RuntimeError):
    """Karakterizasyon sözleşmesi ihlali — koşu reddedilir."""


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def planned_requests(n_items: int,
                     stages: Sequence[str] = STAGES) -> Dict[str, int]:
    """Aşama başına KESİN planlı istek (retry hariç alt sınır)."""
    unknown = [s for s in stages if s not in STAGE_BATCH]
    if unknown:
        raise CharacterizationError(f"bilinmeyen aşama: {unknown}")
    out = {s: math.ceil(n_items / STAGE_BATCH[s]) if n_items else 0
           for s in stages}
    out["total"] = sum(out[s] for s in stages)
    return out


def stage_unit_costs(db, model: str,
                     stages: Sequence[str] = STAGES,
                     price_model: Optional[str] = None
                     ) -> Dict[str, Dict[str, Any]]:
    """Geçmiş telemetriden istek başına USD DAĞILIMI (TAHMİN, fail-closed).

    `price_model` verilirse (Katman-A aday tahmini): token dağılımı yine
    `model` telemetrisinden gelir ama fiyat `price_model`in GÜNCEL fiyat
    tablosu satırıdır — aday kanalın kendi telemetrisi yokken tek dürüst
    tahmin "referans token ortalamaları × aday fiyatları"dır ve bu bir
    TAHMİNDİR (cost_plan zaten ESTIMATE etiketler). Fiyat tablosunda
    `price_model` yoksa fail-closed.
    """
    from app.database.models import AiUsageEvent

    cand_price = None
    if price_model is not None:
        from app.core.telemetry.usage import price_for

        cand_price = price_for(price_model)
        if not cand_price:
            raise CharacterizationError(
                f"FAIL-CLOSED: '{price_model}' fiyat tablosunda yok — "
                f"aday maliyet tahmini uydurulamaz")
    out: Dict[str, Dict[str, Any]] = {}
    for stage in stages:
        rows = (db.query(AiUsageEvent)
                .filter(AiUsageEvent.stage == stage,
                        AiUsageEvent.model == model,
                        AiUsageEvent.price_snapshot.isnot(None),
                        AiUsageEvent.prompt_tokens.isnot(None),
                        # Codex Katman-A #4: candidates'ı olmayan satır
                        # çıktıyı 0 sayıp tahmini iyimserleştirir —
                        # örneklem TAM usage'lı satırlardan kurulur
                        AiUsageEvent.candidates_tokens.isnot(None))
                .all())
        if not rows:
            raise CharacterizationError(
                f"FAIL-CLOSED: '{stage}' + '{model}' için telemetri yok — "
                f"maliyet tahmini uydurulamaz")
        per = []
        for r in rows:
            if cand_price is not None:
                in_per_m = cand_price["input"]
                out_per_m = cand_price["output"]
            else:
                snap = r.price_snapshot or {}
                in_per_m = snap.get("input_per_m", 0)
                out_per_m = snap.get("output_per_m", 0)
            out_tok = (r.candidates_tokens or 0) + (r.thoughts_tokens or 0)
            per.append((r.prompt_tokens or 0) / 1e6 * in_per_m
                       + out_tok / 1e6 * out_per_m)
        per.sort()
        out[stage] = {
            "mean_usd": sum(per) / len(per),
            "p90_usd": per[min(len(per) - 1, int(len(per) * 0.9))],
            "samples": len(per),
        }
        if price_model is not None:
            out[stage]["basis"] = (
                f"TAHMİN: '{model}' token dağılımı × '{price_model}' "
                f"güncel fiyatları (aday kanal telemetrisi yok)")
    return out


def cost_plan(planned: Dict[str, int], unit: Dict[str, Dict[str, Any]],
              repeats: int) -> Dict[str, Any]:
    """Beklenen maliyet + retry payıyla TAVAN (hepsi TAHMİN)."""
    expected = sum(planned[s] * unit[s]["mean_usd"] for s in unit)
    p90 = sum(planned[s] * unit[s]["p90_usd"] for s in unit)
    return {
        "per_repeat_expected_usd": round(expected, 4),
        "per_repeat_p90_usd": round(p90, 4),
        "repeats": repeats,
        "total_expected_usd": round(expected * repeats, 4),
        "total_ceiling_usd": round(p90 * repeats * RETRY_CEILING_FACTOR, 4),
        "ceiling_basis": (
            f"p90 × {RETRY_CEILING_FACTOR} retry payı — geçmiş telemetri "
            f"TAHMİNİ, gerçek fatura değil"),
    }


def freeze_context(db, workspace_id: int) -> Dict[str, Any]:
    """Ürün tanımı + marka bağlamı TEK SEFER çözülür, SHA ile mühürlenir.

    Canlı 'son run' okuması koşu SIRASINDA değişebilir (Codex bulgusu);
    burada kaynak run kimliği + içerik hash'i kayda girer, koşu boyunca
    ve tekrarlar arasında AYNI bağlam kullanılır.
    """
    from app.core.channel.brand_defense import load_product_definition
    from app.database.models import BrandProfile, ScoringRun

    run = (db.query(ScoringRun)
           .filter(ScoringRun.brand_profile_id == workspace_id)
           .order_by(ScoringRun.created_at.desc()).first())
    if run is None:
        raise CharacterizationError(
            f"FAIL-CLOSED: workspace {workspace_id} için run yok — "
            f"ürün tanımı bağlamı dondurulamaz")
    product_definition = load_product_definition(db, run) or ""

    profile = (db.query(BrandProfile)
               .filter(BrandProfile.id == workspace_id,
                       BrandProfile.status == "confirmed",
                       BrandProfile.deleted_at.is_(None))
               .first())
    profile_data = (profile.profile_data
                    if profile and isinstance(profile.profile_data, dict)
                    else {})
    exclude_themes = [str(t).strip() for t in
                      (profile_data.get("exclude_themes") or []) if t]
    ctx = {
        "workspace_id": workspace_id,
        "source_run_id": run.id,
        "source_run_created_at": (run.created_at.isoformat()
                                  if run.created_at else None),
        "profile_confirmed": profile is not None,
        "product_definition": product_definition,
        "profile_data": profile_data,
        "exclude_themes": exclude_themes,
    }
    ctx["context_sha256"] = _sha({
        "product_definition": product_definition,
        "profile_data": profile_data,
        "exclude_themes": exclude_themes,
    })
    return ctx


def fixture_fingerprint(items: Sequence[Dict[str, Any]],
                        label_schema: Dict[str, list]) -> Dict[str, Any]:
    return {
        "items": len(items),
        "input_sha256": _sha([[it["item_id"], it["keyword"]] for it in items]),
        # Codex #3: etkin (adjudicated) etiketler + evaluation_mask da
        # kimliğin parçasıdır — cevap anahtarı değişirse parmak izi değişir
        "labels_mask_sha256": _sha([
            [it["item_id"], it.get("labels"), it.get("evaluation_mask")]
            for it in items]),
        "label_schema_sha256": _sha(
            {k: list(v) for k, v in label_schema.items()}),
    }


def repeat_flip_report(preds_by_repeat: Dict[str, Dict[str, dict]],
                       item_ids: Sequence[str]) -> Dict[str, Any]:
    """Tekrarlar arası KARAR DEĞİŞİMİ (referans kararsızlık dağılımı).

    Çiftler bazında: iki tekrar da değer üretmişse ve değerler farklıysa
    flip; biri üretmemişse `coverage_gap` (ayrı sayılır, flip'e karışmaz).
    """
    labels = sorted(preds_by_repeat)
    pairs = [(a, b) for i, a in enumerate(labels) for b in labels[i + 1:]]
    if not pairs:
        raise CharacterizationError("en az iki tekrar gerekir")
    out: Dict[str, Any] = {"pairs": {}, "per_axis_max_flip": {}}
    per_axis_rates: Dict[str, List[float]] = {a: [] for a in FLIP_AXES}
    for a, b in pairs:
        pa, pb = preds_by_repeat[a], preds_by_repeat[b]
        pair_key = f"{a}<->{b}"
        axes_blk = {}
        for axis in FLIP_AXES:
            compared = flips = gaps = 0
            for item in item_ids:
                va = (pa.get(item) or {}).get(axis)
                vb = (pb.get(item) or {}).get(axis)
                if va is None and vb is None:
                    continue
                if va is None or vb is None:
                    gaps += 1
                    continue
                compared += 1
                if va != vb:
                    flips += 1
            rate = round(flips / compared, 4) if compared else None
            axes_blk[axis] = {"compared": compared, "flips": flips,
                              "flip_rate": rate, "coverage_gap": gaps}
            if rate is not None:
                per_axis_rates[axis].append(rate)
        out["pairs"][pair_key] = axes_blk
    out["per_axis_max_flip"] = {
        axis: (max(rates) if rates else None)
        for axis, rates in per_axis_rates.items()
    }
    return out


def derived_allowances(flip_report: Dict[str, Any],
                       axis_reports_by_repeat: Dict[str, dict]) -> Dict[str, Any]:
    """§6.3 mekanik kuralı: aday payları REFERANS dağılımından türetilir.

    Aday sonucu görülmeden, koşu artifact'ına yazılır; sonradan
    değiştirilemez. F1 aralığı bilgilendirmedir (kapı D-harness'ta).
    """
    allowances = {}
    for axis, max_flip in flip_report["per_axis_max_flip"].items():
        allowances[axis] = {
            "reference_max_flip": max_flip,
            "candidate_flip_allowance": (
                round(max_flip + FLIP_MARGIN, 4)
                if max_flip is not None else None),
        }
    f1_ranges = {}
    for axis in ("intent_type", "ads_class", "opinion_discussion",
                 "curiosity_comparison", "agenda_theme"):
        vals = [rep.get(axis, {}).get("macro_f1")
                for rep in axis_reports_by_repeat.values()]
        vals = [v for v in vals if v is not None]
        if vals:
            f1_ranges[axis] = {"min": round(min(vals), 4),
                               "max": round(max(vals), 4)}
    return {
        "rule": f"aday flip izni = referans maks + {FLIP_MARGIN} "
                f"(önceden yazılmış marj)",
        "per_axis": allowances,
        "reference_macro_f1_range": f1_ranges,
    }


def seal(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc.pop("payload_sha256", None)
    doc["payload_sha256"] = _sha(doc)
    return doc


def verify_seal(doc: Dict[str, Any]) -> None:
    expected = doc.get("payload_sha256")
    body = {k: v for k, v in doc.items() if k != "payload_sha256"}
    if not expected or _sha(body) != expected:
        raise CharacterizationError("karakterizasyon artifact mührü geçersiz")


# ── Codex 2. tur sertleştirmeleri ────────────────────────────────────────
# 1) GERÇEK hard-cap: her provider çağrısından ÖNCE atomik rezervasyon.
# 2) Onay bağlama: dry-run planı mühürlü artifact; execute run-id + context
#    SHA + plan eşleşmesi olmadan BAŞLAYAMAZ.
# 3) Parmak izi etkin etiket + maskeyi de kapsar.
# 4) Prompt/şema/route/thinking provenance hash'leri.

import threading


# Codex 4. tur #1 çözümü — (a) şıkkı SPIKE İLE KANITLANDI
# (benchmark/gemini_thinking_spike.json): Gemini'de `max_output_tokens`
# THINKING DAHİL toplam çıktıyı sınırlar (P4: max_tokens=64 → thoughts 60'ta
# kesildi, finish=MAX_TOKENS; P5: orta boy doğrulama). Dolayısıyla
# rezervasyonun çıktı tarafı = çağrının max_tokens'ı — sağlayıcı-zorlamalı
# GERÇEK üst sınır; ayrıca thinking payı/thinking_budget GEREKMEZ (üretim
# paritesi de korunur: level=low aynen). API zaten level+budget'ı birlikte
# kabul etmiyor (P2/P3 400). settle'daki invaryant bu sözleşmenin bekçisidir:
# actual > reservation görülürse ortak-sınır sözleşmesi delinmiş demektir,
# koşu ANINDA durur.
THINKING_OUTPUT_ALLOWANCE_TOKENS = 0  # çıktı sınırı max_tokens'ın KENDİSİ
OUTPUT_BOUND_PROOF_ARTIFACT = "benchmark/gemini_thinking_spike.json"
# DeepSeek çıktı sınırı kanıtı (Codex Katman-A #1): P4 finish=length'te
# completion=400 (reasoning=400 DAHİL) tavanda kesildi; P5 max_tokens=16
# → completion=16. Yani max_tokens completion'ı reasoning DAHİL sınırlar.
DEEPSEEK_OUTPUT_BOUND_PROOF_ARTIFACT = "benchmark/v4pro_capability_spike.json"
DEFAULT_MAX_TOKENS_FALLBACK = 6000  # scoped.complete_json imza varsayılanı


class CharacterizationBudget:
    """GERÇEK reserve/settle freni (screening ScreeningBudget deseni).

    Codex 3. tur #1: p90 rezervasyonu İSTATİSTİKTİ. Doğru desen:
    - Rezervasyon = provider'a gönderilen TAM request zarfının boyutu
      (nihai contents + JSON talimatı + response schema; UTF-8 bayt >= token)
      + çağrının max_tokens'ı üzerinden EN KÖTÜ maliyet.
    - Yanıt gelince GERÇEK usage ile settle edilir.
    - Usage yoksa rezervasyonun TAMAMI harcanmış sayılır (ceiling charge).
    - `istek sayısı` ve `settled+reserved+sıradaki tavan <= cap` kontrolü
      çağrıdan ÖNCE, kilit altında.
    """

    def __init__(self, max_requests: int, max_cost_usd: float, model: str,
                 thinking_allowance_tokens: int =
                 THINKING_OUTPUT_ALLOWANCE_TOKENS):
        from app.core.telemetry.usage import price_for

        self.max_requests = int(max_requests)
        self.max_cost_usd = float(max_cost_usd)
        self.model = model
        self.thinking_allowance_tokens = int(thinking_allowance_tokens)
        price = price_for(model)
        if not price:
            raise CharacterizationError(
                "HARD-CAP kurulamadı: '%s' için fiyat tablosu girdisi yok"
                % model)
        self._in_per_m = float(price["input"])
        self._out_per_m = float(price["output"])
        self._lock = threading.Lock()
        self._requests = 0
        self._settled = 0.0
        self._reserved = 0.0
        self._ceiling_charges = 0

    def per_call_ceiling_usd(self, request_input_bytes: int,
                             max_tokens: int) -> float:
        """Tek çağrının EN KÖTÜ maliyeti.

        Girdi: tam request zarfının UTF-8 bayt sayısı token sayısının üst
        sınırıdır (her token >= 1 bayt). Çıktı: çağrının max_tokens'ı.
        """
        # max_output_tokens thinking DAHİL toplamı sınırlar (spike-kanıtlı,
        # OUTPUT_BOUND_PROOF_ARTIFACT) — allowance varsayılan 0'dır.
        worst_out = max_tokens + self.thinking_allowance_tokens
        return (request_input_bytes / 1e6 * self._in_per_m
                + worst_out / 1e6 * self._out_per_m)

    def reserve(self, request_input_bytes: int, max_tokens: int) -> float:
        ceiling = self.per_call_ceiling_usd(request_input_bytes, max_tokens)
        with self._lock:
            if self._requests + 1 > self.max_requests:
                raise CharacterizationError(
                    "HARD-CAP: istek sayısı %d+1 > %d — çağrı YAPILMADI"
                    % (self._requests, self.max_requests))
            committed = self._settled + self._reserved + ceiling
            if committed > self.max_cost_usd:
                raise CharacterizationError(
                    "HARD-CAP: taahhüt $%.4f (kesin $%.4f + rezerve $%.4f "
                    "+ sıradaki tavan $%.4f) > $%s — çağrı YAPILMADI"
                    % (committed, self._settled, self._reserved, ceiling,
                       self.max_cost_usd))
            self._requests += 1
            self._reserved += ceiling
            return ceiling

    def settle(self, reservation_usd: float,
               actual_usd: Optional[float]) -> None:
        """Rezervasyonu kapatır: gerçek usage varsa onunla, yoksa tavanla.

        İNVARYANT (Codex 4. tur #1): rezervasyon sağlayıcı-zorlamalı
        tavandan hesaplandığı için actual > reservation OLAMAZ; olursa
        sözleşme (thinking_budget zorlaması) delinmiş demektir — harcama
        kaydedilir ve koşu ANINDA durdurulur.
        """
        with self._lock:
            self._reserved = max(0.0, self._reserved - reservation_usd)
            if actual_usd is None:
                self._settled += reservation_usd
                self._ceiling_charges += 1
                return
            actual = max(0.0, float(actual_usd))
            self._settled += actual
            if actual > reservation_usd * (1 + 1e-6):
                raise CharacterizationError(
                    "İNVARYANT İHLALİ: gerçek maliyet $%.6f > rezervasyon "
                    "$%.6f — sağlayıcı thinking_budget zorlaması delinmiş; "
                    "koşu durduruldu, artifact yazılmayacak"
                    % (actual, reservation_usd))

    @property
    def requests_used(self) -> int:
        return self._requests

    @property
    def cost_settled_usd(self) -> float:
        return round(self._settled, 6)

    @property
    def cost_committed_usd(self) -> float:
        return round(self._settled + self._reserved, 6)

    def summary(self) -> Dict[str, Any]:
        return {
            "requests_used": self._requests,
            "max_requests": self.max_requests,
            "cost_settled_usd": self.cost_settled_usd,
            "cost_committed_usd": self.cost_committed_usd,
            "ceiling_charges": self._ceiling_charges,
            "max_cost_usd": self.max_cost_usd,
            "thinking_allowance_tokens": self.thinking_allowance_tokens,
            "accounting": (
                "reserve = tam request zarfı UTF-8 bayt (provider'ın "
                "GERÇEK payload üreticisiyle aynı kaynak: gemini = "
                "nihai contents + JSON talimatı + schema; deepseek = "
                "OpenAI-uyumlu gövde, şema gönderilmez) + max_tokens; "
                "settle = gerçek usage, usage yoksa/kısmi geçersizse "
                "tavan (ceiling charge)"),
        }


def _events_cost_usd(events, in_per_m: float,
                     out_per_m: float) -> Optional[float]:
    """Collector event'lerinden GERÇEK maliyet; token yoksa None.

    Codex 4. tur #1: KISMİ usage gerçek maliyet DEĞİLDİR. Bir event
    ancak prompt_tokens VE candidates_tokens mevcutsa sayılır
    (thoughts None→0 meşru: model düşünmeden cevaplayabilir). Kısmi
    event any_usage'ı da tetiklemez — çağrıda hiç tam-usage'lı event
    yoksa None döner ve settle rezervasyon TAVANINI yakar. Önceki
    `pt is None and not ct` kuralı çıktı-tokenli ama girdi-tokensız
    event'in kısmi maliyetini gerçek sayıp tavanı söndürüyordu.
    """
    total, any_usage = 0.0, False
    for e in events:
        pt = e.get("prompt_tokens")
        cand = e.get("candidates_tokens")
        if pt is None or cand is None:
            continue
        any_usage = True
        ct = cand + (e.get("thoughts_tokens") or 0)
        total += pt / 1e6 * in_per_m + ct / 1e6 * out_per_m
    return total if any_usage else None


class _GuardedScoped:
    """Stage-scoped AI servisini sarar: çağrı ÖNCESİ en-kötü rezervasyon,
    çağrı SONRASI gerçek usage ile settle (usage yoksa tavan yanar)."""

    def __init__(self, inner, budget, stage, collector=None):
        self._inner = inner
        self._budget = budget
        self._stage = stage
        self._collector = collector

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _guarded(self, method, *args, json_mode: bool, **kwargs):
        import inspect

        from app.generators.ai_service import (
            gemini_request_input_ceiling_bytes,
        )

        try:
            bound = inspect.signature(method).bind_partial(*args, **kwargs)
            bound.apply_defaults()
            call_args = bound.arguments
        except (TypeError, ValueError):
            call_args = kwargs
        variadic_kwargs = call_args.get("kwargs")
        if isinstance(variadic_kwargs, dict):
            call_args = {**variadic_kwargs, **call_args}
        prompt = call_args.get("prompt", args[0] if args else "")
        max_tokens = call_args.get(
            "max_tokens", DEFAULT_MAX_TOKENS_FALLBACK)
        response_schema = call_args.get("response_schema")
        # Codex Katman-A #1: zarf PROVIDER-AWARE — deepseek çağrısının
        # tavanı gerçek DeepSeek gövde üreticisinden hesaplanır (Gemini
        # zarfı farklı bir isteğin baytlarıdır)
        provider = getattr(self._inner, "provider", None) or "gemini"
        if provider == "deepseek":
            from app.generators.ai_service import (
                deepseek_request_input_ceiling_bytes,
            )

            ds_model = (call_args.get("model")
                        or getattr(self._inner, "model", None))
            if not ds_model:
                raise CharacterizationError(
                    "deepseek rezervasyon zarfı için model çözülemedi — "
                    "fail-closed (tahmin yok)")
            # Zarf, GERCEK istekle ayni olmali: thinking acikken govde
            # farkli baytlar tasir (Codex Katman-A: tek kaynak).
            from app.generators.ai_service import _deepseek_thinking_on

            ds_thinking = _deepseek_thinking_on(
                call_args.get("thinking_level")
                or getattr(self._inner, "thinking_level", None))
            request_input_bytes = deepseek_request_input_ceiling_bytes(
                str(prompt), json_mode=json_mode, model=ds_model,
                max_tokens=max_tokens,
                temperature=call_args.get("temperature", 0.3),
                thinking=ds_thinking)
        else:
            request_input_bytes = gemini_request_input_ceiling_bytes(
                str(prompt),
                json_mode=json_mode,
                response_schema=response_schema,
            )
        before = (len(self._collector._all_events)
                  if self._collector is not None else None)
        reservation = self._budget.reserve(request_input_bytes, max_tokens)
        try:
            return method(*args, **kwargs)
        finally:
            actual = None
            if before is not None:
                new_events = self._collector._all_events[before:]
                actual = _events_cost_usd(
                    new_events, self._budget._in_per_m,
                    self._budget._out_per_m)
            self._budget.settle(reservation, actual)

    def complete_json(self, *args, **kwargs):
        return self._guarded(
            self._inner.complete_json, *args, json_mode=True, **kwargs)

    def complete(self, *args, **kwargs):
        return self._guarded(
            self._inner.complete, *args, json_mode=False, **kwargs)


class BudgetGuardedAI:
    """ModelPinnedAI'yi bütçe frenli scoped servislerle sarar.

    Katman kurucuları scoped(ai, stage) → for_stage çağırır; kesişim
    noktası burasıdır — üretim katman kodu DEĞİŞMEZ. `collector`
    verilirse settle GERÇEK usage'la yapılır; verilmezse her çağrı
    tavandan yanar (daha da konservatif).
    """

    def __init__(self, pinned, budget, collector=None,
                 thinking_budget=None):
        self._pinned = pinned
        self._budget = budget
        self._collector = collector
        self._thinking_budget = thinking_budget

    @property
    def collector(self):
        return getattr(self._pinned, "collector", None)

    def for_stage(self, stage, **overrides):
        if (self._thinking_budget is not None
                and "thinking_budget" not in overrides):
            overrides["thinking_budget"] = self._thinking_budget
        return _GuardedScoped(self._pinned.for_stage(stage, **overrides),
                              self._budget, stage,
                              collector=self._collector)

    def __getattr__(self, name):
        return getattr(self._pinned, name)


def prompt_config_hashes() -> Dict[str, Any]:
    """Plan §5 sözleşmesi: prompt/şema/route/thinking provenance.

    Prompt'lar batch'e göre kurulduğundan ÜRETİCİ KAYNAĞI hash'lenir
    (builder kaynak kodu değişirse hash değişir); şemalar doğrudan.
    Erişilemeyen alan 'UNAVAILABLE:...' olarak DÜRÜSTÇE kaydedilir.
    """
    import inspect

    out: Dict[str, Any] = {"prompt_source_sha256": {},
                           "response_schema_sha256": {}}

    def add_src(name, fn):
        try:
            out["prompt_source_sha256"][name] = _sha(inspect.getsource(fn))
        except Exception as exc:  # noqa: BLE001
            out["prompt_source_sha256"][name] = ("UNAVAILABLE:%s" % exc)[:80]

    def add_schema(name, obj):
        try:
            out["response_schema_sha256"][name] = (_sha(obj) if obj
                                                   else "absent")
        except Exception as exc:  # noqa: BLE001
            out["response_schema_sha256"][name] = ("UNAVAILABLE:%s" % exc)[:80]

    try:
        from app.generators.ai_service import (
            build_deepseek_request_payload,
            build_gemini_contents,
            deepseek_request_input_ceiling_bytes,
            gemini_request_input_ceiling_bytes,
        )

        out["hard_cap_contract"] = {
            "build_contents_source_sha256": _sha(
                inspect.getsource(build_gemini_contents)),
            "input_ceiling_source_sha256": _sha(
                inspect.getsource(gemini_request_input_ceiling_bytes)),
            "output_bound_artifact": OUTPUT_BOUND_PROOF_ARTIFACT,
            # Codex Katman-A #1: deepseek zarfı da provenance'a bağlanır
            "deepseek_payload_source_sha256": _sha(
                inspect.getsource(build_deepseek_request_payload)),
            "deepseek_input_ceiling_source_sha256": _sha(
                inspect.getsource(deepseek_request_input_ceiling_bytes)),
            "deepseek_output_bound_artifact":
                DEEPSEEK_OUTPUT_BOUND_PROOF_ARTIFACT,
        }
    except Exception as exc:  # noqa: BLE001
        out["hard_cap_contract"] = {
            "error": ("UNAVAILABLE:%s" % exc)[:120],
        }

    try:
        from app.core.channel.brand_filter import BrandExclusionFilter
        from app.core.channel.intent_analyzer import IntentAnalyzer
        from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
        from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
        from app.core.channel.pre_filters.social_prefilter import (
            SocialPreFilter,
        )

        add_src("intent", IntentAnalyzer._build_intent_prompt)
        add_src("ads_prefilter", AdsPreFilter._build_filter_prompt)
        add_src("social_prefilter", SocialPreFilter._build_filter_prompt)
        add_src("seo_metadata", SeoPreFilter._build_filter_prompt)
        add_src("brand_filter", BrandExclusionFilter._build_prompt)
        add_schema("intent_base", IntentAnalyzer.RESPONSE_SCHEMA_BASE)
        add_schema("intent_seo", IntentAnalyzer.RESPONSE_SCHEMA_SEO)
        add_schema("ads_prefilter", AdsPreFilter.RESPONSE_SCHEMA)
        add_schema("social_prefilter", SocialPreFilter.RESPONSE_SCHEMA)
        add_schema("seo_metadata", SeoPreFilter.RESPONSE_SCHEMA)
        add_schema("brand_filter", BrandExclusionFilter.RESPONSE_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        out["import_error"] = str(exc)[:200]

    try:
        from app.config import settings

        out["route_config"] = dict(getattr(settings, "AI_STAGE_MODELS", {})
                                   or {})
        out["gemini_thinking_level"] = getattr(
            settings, "GEMINI_THINKING_LEVEL", None)
    except Exception as exc:  # noqa: BLE001
        out["settings_error"] = str(exc)[:200]
    return out


import re as _re

_FULL_SHA = _re.compile(r"^[0-9a-f]{64}$")


def require_full_sha(value: str, label: str) -> str:
    """Codex 4. tur #2: bağlama TAM 64-hane hex SHA ister; önek KABUL
    EDİLMEZ (== karşılaştırması)."""
    v = (value or "").strip().lower()
    if not _FULL_SHA.fullmatch(v):
        raise CharacterizationError(
            "%s TAM 64 hane hex olmalı (önek kabul edilmez); verilen: %r"
            % (label, value))
    return v


def build_plan_doc(*, model, repeats, workspace_id, source_run_id,
                   context_sha256, fingerprint, planned, plan, caps,
                   active_stages, skipped, generated_at,
                   provider="gemini"):
    """Dry-run planı MÜHÜRLÜ artifact olur — onay tam olarak buna verilir.

    `kind` her rol için aynıdır (tek plan şeması); rol/sağlayıcı ayrımını
    `provider` alanı + dosya adı taşır. Eski (provider alansız) planlar
    gemini sayılır — deepseek execute'u eski plana bağlanamaz.
    """
    return seal({
        "kind": "reference_characterization_plan",
        "provider": provider,
        "model": model, "repeats": repeats,
        "workspace_id": workspace_id,
        "source_run_id": source_run_id,
        "context_sha256": context_sha256,
        "fixture": fingerprint,
        "planned_requests_per_repeat": planned,
        "cost_plan": plan,
        "caps": caps,
        "active_stages": list(active_stages),
        "skipped_stages": list(skipped),
        "prompt_config": prompt_config_hashes(),
        "generated_at": generated_at,
    })


def verify_plan_binding(plan_doc, *, model, repeats, source_run_id,
                        context_sha256, fingerprint, planned, caps,
                        workspace_id=None, active_stages=None,
                        skipped=None, plan=None, prompt_config=None,
                        expected_plan_sha=None, provider=None):
    """Execute, ONAYLANAN plana bire bir bağlanır (Codex #2 + 3. tur #3).

    Seal + alan eşitliği; herhangi bir sapma provider çağrısından ÖNCE
    reddedilir: bağlam kayması, fixture değişimi, cap oynaması, PROMPT/
    ŞEMA/ROUTE değişimi, aşama seti ve maliyet planı dahil. Ayrıca
    `expected_plan_sha` verilirse plan mührünün kendisi de onaya bağlanır
    (aynı run/context ile plan sessizce değiştirilemez).
    """
    verify_seal(plan_doc)
    if plan_doc.get("kind") != "reference_characterization_plan":
        raise CharacterizationError("plan artifact türü yanlış")
    if expected_plan_sha is not None:
        actual_sha = plan_doc.get("payload_sha256") or ""
        expected = require_full_sha(expected_plan_sha, "plan SHA")
        if actual_sha != expected:
            raise CharacterizationError(
                "PLAN BAĞLAMA REDDİ — plan SHA uyuşmuyor: onaylanan %s..., "
                "diskteki %s..." % (expected[:16], actual_sha[:16]))
    checks = [
        ("model", plan_doc.get("model"), model),
        ("repeats", plan_doc.get("repeats"), repeats),
        ("source_run_id", plan_doc.get("source_run_id"), source_run_id),
        ("context_sha256", plan_doc.get("context_sha256"), context_sha256),
        ("fixture", plan_doc.get("fixture"), fingerprint),
        ("planned_requests_per_repeat",
         plan_doc.get("planned_requests_per_repeat"), planned),
        ("caps", plan_doc.get("caps"), caps),
    ]
    if workspace_id is not None:
        checks.append(("workspace_id", plan_doc.get("workspace_id"),
                       workspace_id))
    if active_stages is not None:
        checks.append(("active_stages", plan_doc.get("active_stages"),
                       list(active_stages)))
    if skipped is not None:
        checks.append(("skipped_stages", plan_doc.get("skipped_stages"),
                       list(skipped)))
    if plan is not None:
        checks.append(("cost_plan", plan_doc.get("cost_plan"), plan))
    if prompt_config is not None:
        checks.append(("prompt_config", plan_doc.get("prompt_config"),
                       prompt_config))
    if provider is not None:
        # Eski planlar provider alansız → gemini sayılır; deepseek koşusu
        # eski/yanlış-provider plana bağlanamaz (fail-closed)
        checks.append(("provider", plan_doc.get("provider", "gemini"),
                       provider))
    mismatches = ["%s: plan=%r != şimdi=%r" % (n, p, c)
                  for n, p, c in checks if p != c]
    if mismatches:
        raise CharacterizationError(
            "PLAN BAĞLAMA REDDİ — onaylanan dry-run ile koşu girdileri "
            "uyuşmuyor: " + "; ".join(mismatches[:4])
            + (" ... (+%d)" % (len(mismatches) - 4)
               if len(mismatches) > 4 else ""))
