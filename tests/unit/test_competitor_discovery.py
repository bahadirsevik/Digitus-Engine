from types import SimpleNamespace

from app.core.policy.competitor_discovery import (
    _candidate_evidence_urls,
    discover_raw_candidates,
)
from app.generators.ai_service import GroundedCompletion


class GroundingFallbackAI:
    def __init__(self):
        self.grounded_calls = 0
        self.extract_calls = 0

    def for_stage(self, stage, **overrides):
        assert stage == "competitor_discovery"
        return self

    def complete_grounded(self, prompt, max_tokens=4000, temperature=0.2, response_schema=None):
        self.grounded_calls += 1
        if response_schema is not None:
            raise RuntimeError("tool + schema unsupported")
        return GroundedCompletion(
            text="Araştırmada Rakip bulundu: https://rakip.com",
            search_queries=["örnek rakipleri"],
            evidence_urls=["https://rakip.com"],
        )

    def complete_json(self, prompt, max_tokens=6000, temperature=0.3, response_schema=None):
        self.extract_calls += 1
        return '{"candidates":[{"name":"Rakip","url":"https://rakip.com","rationale":"Aynı ürün"}]}'


def test_two_step_grounding_fallback_has_same_result_contract():
    ai = GroundingFallbackAI()
    workspace = SimpleNamespace(
        company_url="https://ornek.com",
        competitor_urls=[],
        competitor_terms=[],
        default_geo_target_id="2792",
        default_language_id="1037",
        profile_data={
            "company_name": "Örnek",
            "sector": "finans",
            "brand_summary": "Analiz ürünü",
            "products": ["Analiz"],
            "services": [],
            "target_audience": "Yatırımcı",
        },
    )
    result = discover_raw_candidates(ai, workspace)
    assert result["fallback_used"] is True
    assert result["candidates"][0]["name"] == "Rakip"
    assert result["search_queries"] == ["örnek rakipleri"]
    assert ai.grounded_calls == 2
    assert ai.extract_calls == 1


def test_candidate_evidence_uses_only_matching_support_segment():
    supports = [
        {"text": "Rakip A doğrudan alternatiftir", "evidence_urls": ["source-a"]},
        {"text": "Başka Marka yakın bir çözümdür", "evidence_urls": ["source-b"]},
    ]
    assert _candidate_evidence_urls("Rakip A", "rakipa.com", supports) == ["source-a"]
    assert _candidate_evidence_urls("İlgisiz", "ilgisiz.com", supports) == []
