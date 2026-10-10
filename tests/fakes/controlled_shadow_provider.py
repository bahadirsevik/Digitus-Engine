"""Kontrollü shadow koşusu için SAHTE pipeline sağlayıcısı (ağ çağrısı YOK).

Tek sağlayıcı üç aşamayı da karşılar: prompt'a bakarak intent / brand /
ADS ön-filtre yanıtı üretir. `fail_stage_units` ile belirli aşamada ilk N
birimde bozuk yanıt döndürerek retry sınırları test edilir.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from tests.fakes.gr7_micro_experiment_provider import (
    DEFAULT_FAKE_MODEL, FakeUsageCollector, _FakeResponse, build_ads_prefilter_response,
    build_batch_response,
)

_INTENT_ID_RE = re.compile(r"^- (\d+): (.+)$", re.MULTILINE)


def detect_stage(prompt: str) -> str:
    if "marka uygunlu" in prompt.lower() or "exclude_th" in prompt:
        return "brand_filter"
    if "Google Ads ön filtresi" in prompt or "hot_sale" in prompt:
        return "ads_prefilter"
    return "intent"


def build_intent_response(prompt: str) -> str:
    rows = _INTENT_ID_RE.findall(prompt)
    results = []
    for keyword_id, keyword in rows:
        results.append({
            "keyword_id": int(keyword_id),
            "intent_type": "commercial",
            "confidence_score": 0.9,
            "reasoning": "sahte saglayici",
            "gt": True,
            "ga": True,
            "strategy_fit": True,
        })
    return json.dumps({"results": results})


def build_response(prompt: str) -> str:
    stage = detect_stage(prompt)
    if stage == "brand_filter":
        return build_batch_response(prompt)
    if stage == "ads_prefilter":
        return build_ads_prefilter_response(prompt)
    return build_intent_response(prompt)


class FakePipelineProvider:
    """Üç aşamayı da karşılayan sahte sağlayıcı."""

    provider = "gemini"

    def __init__(self, *, model_name: str = DEFAULT_FAKE_MODEL,
                 fail_stage_units: Dict[str, int] | None = None):
        self.model_name = model_name
        self.collector = FakeUsageCollector()
        self.calls = 0
        self.calls_by_stage: Dict[str, int] = {}
        self.fail_stage_units = dict(fail_stage_units or {})
        self._units_seen: Dict[str, int] = {}
        self._last_prompt_by_stage: Dict[str, str] = {}

    def for_stage(self, stage: str, **overrides: Any) -> "FakePipelineProvider":
        return self

    def complete_json(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        stage = detect_stage(prompt)
        self.calls_by_stage[stage] = self.calls_by_stage.get(stage, 0) + 1
        self.collector.record(
            stage=stage, model=self.model_name,
            prompt_tokens=max(1, len(prompt) // 4),
            candidates_tokens=120, thoughts_tokens=200)
        budget = self.fail_stage_units.get(stage)
        if budget:
            # AYNI birimde (ayni prompt) BOZUK yanit -> retry tetikler
            if self._last_prompt_by_stage.get(stage) != prompt:
                self._last_prompt_by_stage[stage] = prompt
                self._units_seen[stage] = self._units_seen.get(stage, 0) + 1
            if self._units_seen.get(stage, 0) <= budget:
                return "BU GECERLI JSON DEGIL"
        return build_response(prompt)


class _PipelineModels:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_models: List[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.seen_models.append(model)
        return _FakeResponse(build_response(str(contents)))


class FakePipelineClient:
    def __init__(self) -> None:
        self.models = _PipelineModels()


def build_fake_pipeline_client() -> FakePipelineClient:
    return FakePipelineClient()
