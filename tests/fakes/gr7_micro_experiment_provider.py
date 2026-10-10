"""Mikro deney için SAHTE provider — gerçek API çağrısı YOK.

Üretimdeki `ModelPinnedAI` yüzeyini taklit eder: `for_stage()`, `provider`
ve bir `collector`. Böylece runner, repo'nun KENDİ `BudgetGuardedAI` +
`_GuardedScoped` desenini (gerçek Gemini zarfı ile rezervasyon, collector
event'lerinden GERÇEK usage ile settle) sahte sağlayıcıyla da koşabilir.

`fail_batches_above` split-retry zincirini tetikler; `emit_usage=False` ise
usage YOKTUR ve rezervasyon tavanı yanar (ceiling charge yolu).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_KEYWORDS_RE = re.compile(r"keywords=(\[.*\])\s*\n\nSADECE", re.DOTALL)


def build_batch_response(prompt: str, *,
                         exclude_keywords: set | None = None,
                         emit_protected_field: bool = True) -> str:
    """Prompt'taki batch'e ŞEMA-UYUMLU yanıt üretir (tek üretici)."""
    match = _KEYWORDS_RE.search(prompt)
    batch: List[Dict[str, Any]] = json.loads(match.group(1)) if match else []
    excluded_set = exclude_keywords or set()
    results = []
    for item in batch:
        excluded = item["keyword"] in excluded_set
        row: Dict[str, Any] = {
            "keyword_id": item["id"],
            "is_brand_relevant": not excluded,
            "matched_exclude_theme": "Saç ekimi" if excluded else "",
        }
        if emit_protected_field:
            row["matched_protected_theme"] = ""
        results.append(row)
    return json.dumps({"results": results})


# ── GERÇEK GeminiService için sahte transport ────────────────────────
# `client` property'si monkeypatch edilir; ağa ÇIKILMAZ. Amaç: gerçek
# sınıfın usage event'ini runner'ın bağladığı collector'a yazdığını ve
# settlement'ın tavan yerine ACTUAL olduğunu kanıtlamak.

class _FakeUsage:
    prompt_token_count = 700
    candidates_token_count = 140
    thoughts_token_count = 600
    total_token_count = 1440


class _FakeCandidate:
    finish_reason = "STOP"

    def __init__(self, text: str):
        part = type("P", (), {"text": text})()
        self.content = type("C", (), {"parts": [part]})()


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.candidates = [_FakeCandidate(text)]
        self.usage_metadata = _FakeUsage()


class _FakeModels:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_models: List[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.seen_models.append(model)
        return _FakeResponse(build_batch_response(str(contents)))


class FakeGenaiClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def build_fake_gemini_client() -> FakeGenaiClient:
    return FakeGenaiClient()


class FakeUsageCollector:
    """Üretim `UsageCollector` ile aynı okuma sözleşmesi: `_all_events`."""

    def __init__(self) -> None:
        self._all_events: List[Dict[str, Any]] = []

    def record(self, **event: Any) -> None:
        self._all_events.append(event)


# Deney sozlesmelerinin guncel calisma modeli (3.8 gecisi, 27.09). Tarihsel 3.5
# senaryolari model_name ile acikca 3.5 verir.
DEFAULT_FAKE_MODEL = "gemini-3.8-flash"


class FakeBrandProvider:
    provider = "gemini"

    def __init__(self, *, exclude_keywords: List[str] | None = None,
                 fail_batches_above: int | None = None,
                 emit_protected_field: bool = True,
                 emit_usage: bool = True,
                 model_name: str = DEFAULT_FAKE_MODEL):
        # Kimlik dogrulamasi icin uretimdeki `model_name` alanini tasir;
        # yanlis model senaryosu bunu degistirerek kirmizi yapilabilir.
        self.model_name = model_name
        self.exclude_keywords = set(exclude_keywords or [])
        self.fail_batches_above = fail_batches_above
        self.emit_protected_field = emit_protected_field
        self.emit_usage = emit_usage
        self.calls = 0
        self.seen_batch_sizes: List[int] = []
        self.collector = FakeUsageCollector()

    # ModelPinnedAI yuzeyi: stage'e baglanir, ayni nesneyi dondurur
    def for_stage(self, stage: str, **overrides: Any) -> "FakeBrandProvider":
        return self

    def _record_usage(self, prompt: str, batch_size: int) -> None:
        if not self.emit_usage:
            return
        # Telemetriye yakin, DETERMINISTIK ve tavanin ALTINDA kalan usage
        self.collector.record(
            stage="brand_filter",
            model=self.model_name,
            prompt_tokens=max(1, len(prompt) // 4),
            candidates_tokens=30 + 20 * batch_size,
            thoughts_tokens=400,
        )

    def complete_json(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        match = _KEYWORDS_RE.search(prompt)
        batch: List[Dict[str, Any]] = json.loads(match.group(1)) if match else []
        self.seen_batch_sizes.append(len(batch))
        self._record_usage(prompt, len(batch))
        if (self.fail_batches_above is not None
                and len(batch) > self.fail_batches_above):
            return "BU GECERLI JSON DEGIL"
        return build_batch_response(
            prompt, exclude_keywords=self.exclude_keywords,
            emit_protected_field=self.emit_protected_field)


# ── ADS ön-filtre için sahte sağlayıcı (B executor dry-run/E2E) ──────
def _extract_keyword_batch(prompt: str) -> List[Dict[str, Any]]:
    """`keywords=[...]` bloğunu ayrıştırır.

    ÖNEMLİ: gerçek `GeminiService` prompt'a `JSON_ONLY_SUFFIX` EKLER; bu
    yüzden satır sonuna sabitlenmiş regex kullanılamaz — JSON dizisi
    `raw_decode` ile okunur.
    """
    marker = "keywords="
    index = prompt.rfind(marker)
    if index < 0:
        return []
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(prompt[index + len(marker):].lstrip())
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def build_ads_prefilter_response(prompt: str, *,
                                 eliminate_keywords: List[str] | None = None
                                 ) -> str:
    """ADS ön-filtre şemasına uygun yanıt (ağ çağrısı YOK)."""
    batch = _extract_keyword_batch(prompt)
    drop = set(eliminate_keywords or [])
    results = []
    for item in batch:
        eliminated = item["keyword"] in drop
        results.append({
            "keyword_id": item["id"],
            "decision": "eliminate" if eliminated else "keep",
            "label": None if eliminated else "lead",
            "reason_code": "NEGATIVE_TERM" if eliminated else "CATEGORY_MATCH",
            "reason": "sahte saglayici",
            "transfer_channel": "SEO" if eliminated else None,
        })
    return json.dumps({"results": results})


class FakeAdsPrefilterProvider(FakeBrandProvider):
    """`AdsPreFilter` çağrılarına şema-uyumlu yanıt üretir."""

    def __init__(self, *, eliminate_keywords: List[str] | None = None,
                 **kwargs):
        super().__init__(**kwargs)
        self.eliminate_keywords = eliminate_keywords or []

    def complete_json(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        self._record_usage(prompt, len(_extract_keyword_batch(prompt)))
        return build_ads_prefilter_response(
            prompt, eliminate_keywords=self.eliminate_keywords)


class _FakeAdsModels(_FakeModels):
    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.seen_models.append(model)
        return _FakeResponse(build_ads_prefilter_response(str(contents)))


class FakeAdsGenaiClient:
    def __init__(self) -> None:
        self.models = _FakeAdsModels()


def build_fake_ads_gemini_client() -> FakeAdsGenaiClient:
    return FakeAdsGenaiClient()
