# -*- coding: utf-8 -*-
"""Unit tests for Tek Work Item Generate → Quality → En Fazla Bir Repair Yürütücüsü (F1-G.5.3).

Bu test süiti:
1. Temiz ilk üretim (accept) -> 1 AI çağrısı, repaired=False, accepted
2. İddia içeren ilk üretim -> tek repair başarılı -> 2 AI çağrısı, repaired=True, accepted
3. Süre uyumsuz ilk üretim -> tek repair başarılı -> 2 AI çağrısı, repaired=True, accepted
4. Süre uyumsuz ilk üretim -> repair sonrası süre hâlâ uyumsuz fakat iddia temiz -> accepted + warning (tolerans)
5. İddia içeren ilk üretim -> repair sonrası iddia devam ediyor -> 2 AI çağrısı, repaired=True, rejected (content=None)
6. Birleşik iddia ve süre hatası -> repair ile ikisi birden düzelir -> 2 AI çağrısı, repaired=True, accepted
7. Birleşik hata -> repair ile süre düzelir fakat iddia kalır -> 2 AI çağrısı, repaired=True, rejected (content=None)
8. Önkoşul: Geçersiz work_item nesnesi -> 0 AI çağrısı, content_input_invalid
9. Önkoşul: Geçersiz prompt_input -> 0 AI çağrısı, content_input_invalid
10. Önkoşul: Grounding-prompt parite uyuşmazlığı -> 0 AI çağrısı, content_input_invalid
11. Önkoşul: work_item idea_id uyuşmazlığı -> 0 AI çağrısı, content_input_invalid
12. Önkoşul: ai_service None -> 0 AI çağrısı, content_input_invalid
13. İlk üretimde sağlayıcı hatası (provider error) -> 1 AI çağrısı, content_provider_error
14. İlk üretimde geçersiz çıktı (output invalid) -> 1 AI çağrısı, content_output_invalid
15. Repair sırasında sağlayıcı hatası (repair provider error) -> 2 AI çağrısı, content_repair_provider_error
16. Repair sırasında geçersiz çıktı (repair output invalid) -> 2 AI çağrısı, content_repair_output_invalid
17. Düzeltme kararında forged/sahte nesne tespit edildiğinde -> fail-closed content_execution_inconsistent
18. İkinci repair talebi tespit edildiğinde -> fail-closed content_execution_inconsistent
19. DTO immutability ve invariant kontrolleri (accepted+None, rejected+content, repaired mismatch)
20. Hata sanitizasyonu (prompt, caption, claim, exception sızmama garantisi)
21. Tüm çağrılarda temperature=None sözleşmesi ve maksimum 2 AI çağrısı garantisi
senaryolarını izole sahte AI servisi ile uçtan uca doğrular.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import patch

import pytest

from app.core.social.content_contract import (
    ContentTargetSpec,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
)
from app.core.social.content_item_execution import (
    CONTENT_EXECUTION_INCONSISTENT,
    CONTENT_INPUT_INVALID,
    CONTENT_OUTPUT_INVALID,
    CONTENT_PROVIDER_ERROR,
    CONTENT_QUALITY_INVALID,
    CONTENT_REPAIR_INPUT_INVALID,
    CONTENT_REPAIR_OUTPUT_INVALID,
    CONTENT_REPAIR_PROVIDER_ERROR,
    SocialContentItemExecutionError,
    SocialContentItemExecutionResult,
    execute_social_content_work_item,
)
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    SocialContentQualityDecision,
    evaluate_social_content_quality,
)
from app.core.social.content_worker_input import SocialContentWorkItem
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentRepairAIResult,
)
from app.generators.social.brief_content_prompt import (
    SocialContentKeywordInput,
    SocialContentPromptInput,
    validate_social_content_prompt_input,
)


# ==================== TEST YARDIMCILARI & FAKE AI ====================


class FakeCollector:
    """Telemetri işaretlemelerini toplayan sahte nesne."""

    def __init__(self) -> None:
        self.failed_reasons: list[str] = []

    def mark_current_attempt_failed(self, reason: str) -> bool:
        self.failed_reasons.append(reason)
        return True

    def logical_request(self):
        from contextlib import nullcontext
        return nullcontext()


class FakeAIService:
    """F1-G.5.3 testleri için izole sahte AI servisi."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.responses: list[Any] = list(responses) if responses else []
        self.raise_exc: Exception | None = raise_exc
        self.call_count: int = 0
        self.call_args: list[dict[str, Any]] = []
        self.stage: str | None = None
        self.collector = FakeCollector()

    def for_stage(self, stage: str, **overrides) -> FakeAIService:
        self.stage = stage
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.call_args.append({"prompt": prompt, **kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return json.dumps({"result": "empty"})


def _make_prompt_input(
    *,
    attempt_id: int = 10,
    idea_id: int = 20,
    target_spec: ContentTargetSpec | None = None,
    idea_title: str = "Doğal Bakım Rutini",
    idea_description: str = "Doğal içerikli cilt bakım ürünleri rehberi.",
    primary_keyword: SocialContentKeywordInput | None = None,
    brand_name: str | None = "Acme Kozmetik",
    brand_tone: str | None = "Samimi ve eğitici",
    brand_context: str | None = "Doğal cilt bakım ürünleri",
    product_facts: str | None = "Dermatolojik olarak test edilmiştir.",
    trusted_brand_usp: str | None = "Organik soğuk sıkım yağlar",
) -> SocialContentPromptInput:
    if target_spec is None:
        target_spec = ContentTargetSpec(
            target_id=1,
            platform="instagram",
            content_format="reels",
            duration_preset_id="short_16_30",
            duration_min_sec=16,
            duration_max_sec=30,
        )
    if primary_keyword is None:
        primary_keyword = SocialContentKeywordInput(
            keyword_id=1,
            keyword="organik cilt bakımı",
        )
    return SocialContentPromptInput(
        attempt_id=attempt_id,
        idea_id=idea_id,
        target_spec=target_spec,
        idea_title=idea_title,
        idea_description=idea_description,
        primary_keyword=primary_keyword,
        brand_name=brand_name,
        brand_tone=brand_tone,
        brand_context=brand_context,
        product_facts=product_facts,
        trusted_brand_usp=trusted_brand_usp,
    )


def _make_work_item(
    prompt_input: SocialContentPromptInput | None = None,
    grounding_context: SocialContentGroundingContext | None = None,
    idea_id: int | None = None,
) -> SocialContentWorkItem:
    if prompt_input is None:
        prompt_input = _make_prompt_input()
    if grounding_context is None:
        grounding_context = SocialContentGroundingContext(
            primary_keyword=prompt_input.primary_keyword.keyword,
            product_facts=prompt_input.product_facts,
            trusted_brand_usp=prompt_input.trusted_brand_usp,
        )
    resolved_idea_id = idea_id if idea_id is not None else prompt_input.idea_id
    return SocialContentWorkItem(
        idea_id=resolved_idea_id,
        prompt_input=prompt_input,
        grounding_context=grounding_context,
    )


def _make_valid_video_response_dict(
    *,
    end_sec: int = 24,
    caption: str = "Doğal cilt bakım rutini önerileri.",
    hook_text: str = "Cildiniz için temiz içerikli 3 kural!",
) -> dict[str, Any]:
    return {
        "hooks": [
            {"text": hook_text, "style": "curiosity"},
        ],
        "caption": caption,
        "cta_text": "Detaylar için profildeki linke tıklayın.",
        "hashtags": ["ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik"],
        "format_payload": {
            "kind": "video",
            "segments": [
                {
                    "start_sec": 0,
                    "end_sec": end_sec,
                    "scene": "Açılış ve uygulama planı",
                    "on_screen_text": "Temiz Bakım",
                    "voiceover": "Doğal içerikli adımlarla cildinizi koruyun.",
                },
            ],
        },
        "visual_suggestion": "Pastel ve ferah ışık",
        "video_concept": "B-roll",
        "industry_posting_suggestion": "Akşam 19:00",
        "platform_notes": None,
    }


def _make_valid_post_response_dict(
    *,
    caption: str = "Günlük cilt bakımında dikkat edilmesi gerekenler.",
    hook_text: str = "Cildiniz için doğal dokunuş!",
) -> dict[str, Any]:
    return {
        "hooks": [
            {"text": hook_text, "style": "relatable"},
        ],
        "caption": caption,
        "cta_text": "Yorumlarda deneyimlerinizi paylaşın!",
        "hashtags": ["ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik"],
        "format_payload": None,
        "visual_suggestion": "Minimal ürün çekimi",
        "video_concept": None,
        "industry_posting_suggestion": "Sabah 09:00",
        "platform_notes": None,
    }


# ==================== TESTLER ====================


def test_clean_initial_generation_accepted():
    """Temiz ilk üretimde 1 AI çağrısı ile accept dönülür (repaired=False)."""
    resp_dict = _make_valid_video_response_dict(end_sec=24)
    fake_ai = FakeAIService([json.dumps(resp_dict)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert isinstance(result, SocialContentItemExecutionResult)
    assert result.attempt_id == 10
    assert result.idea_id == 20
    assert result.status == "accepted"
    assert result.content is not None
    assert isinstance(result.content, ValidatedSocialContent)
    assert result.content.actual_duration_sec == 24
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.repair_attempted is False
    assert result.repaired is False
    assert result.ai_calls_used == 1
    assert fake_ai.call_count == 1
    assert fake_ai.call_args[0]["temperature"] is None


def test_ungrounded_claim_repair_accepted():
    """İddia içeren ilk üretim tek repair ile düzeltilip accept edilir (repaired=True, ai_calls=2)."""
    # 1. response: ungrounded claim ("%80 başarı")
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Bu krem ile %80 başarı sağlayan formüle sahip olun!",
    )
    # 2. response: temiz düzeltilmiş içerik
    second_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Doğal içerikli adımlarla cildinize özen gösterin.",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "accepted"
    assert result.content is not None
    assert "%80" not in result.content.caption
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.repair_attempted is True
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2
    for args in fake_ai.call_args:
        assert args["temperature"] is None


def test_duration_mismatch_repair_accepted():
    """Süre uyumsuz ilk üretim tek repair ile süre düzeltilerek accept edilir."""
    # 1. response: 10 saniye (hedef aralık 16-30 saniye -> mismatch)
    first_resp = _make_valid_video_response_dict(end_sec=10)
    # 2. response: 25 saniye (aralık içinde -> valid)
    second_resp = _make_valid_video_response_dict(end_sec=25)
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "accepted"
    assert result.content is not None
    assert result.content.actual_duration_sec == 25
    assert result.content.duration_status == "valid"
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.repair_attempted is True
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_duration_mismatch_repair_tolerated_accepted():
    """Süre uyumsuz ilk üretim sonrası iddia temizse ve süre hâlâ mismatch ise warning ile accept edilir."""
    # 1. response: 10 saniye
    first_resp = _make_valid_video_response_dict(end_sec=10)
    # 2. response: 12 saniye (hâlâ mismatch, fakat iddia temiz)
    second_resp = _make_valid_video_response_dict(end_sec=12)
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "accepted"
    assert result.content is not None
    assert result.content.actual_duration_sec == 12
    assert result.content.duration_status == "mismatch"
    assert result.quality_decision.action == "accept"
    assert "duration_mismatch" in result.quality_decision.warnings
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_ungrounded_claim_repair_still_dirty_rejected():
    """İddia içeren içerik repair sonrasında da iddia barındırıyorsa kesinlikle rejected ve content=None döner."""
    # 1. response: "1 numara cilt bakım ürünü"
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Türkiye'nin 1 numara organik bakım ürünü!",
    )
    # 2. response: "%100 kesin sonuç garantisi"
    second_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Yalnızca 3 günde %100 kesin sonuç garantisi!",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "rejected"
    assert result.content is None  # Güvenlik gereği içerik temizlenir
    assert result.quality_decision.action == "reject"
    assert "ungrounded_claim" in result.quality_decision.reason_codes
    assert len(result.quality_decision.claims) > 0
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_combined_claim_and_duration_repair_accepted():
    """Hem iddia hem süre hatalı içerik tek repair ile ikisini de düzelttiğinde accept edilir."""
    # 1. response: 10s (mismatch) + claim ("50 bin kullanıcı")
    first_resp = _make_valid_video_response_dict(
        end_sec=10,
        caption="50 bin kullanıcı yanılıyor olamaz!",
    )
    # 2. response: 24s (valid) + temiz metin
    second_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Doğal içerikli adımlarla cildinize özen gösterin.",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "accepted"
    assert result.content is not None
    assert result.content.actual_duration_sec == 24
    assert result.quality_decision.action == "accept"
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_combined_claim_and_duration_repair_claim_persists_rejected():
    """Hem iddia hem süre hatalı içerikte süre düzelse bile iddia kalırsa rejected döner."""
    first_resp = _make_valid_video_response_dict(
        end_sec=10,
        caption="50 bin kullanıcı yanılıyor olamaz!",
    )
    # 2. response: süre düzeldi (22s) ama iddia devam ediyor ("%80 başarı")
    second_resp = _make_valid_video_response_dict(
        end_sec=22,
        caption="Yine de %80 başarı sağlayan formül!",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "rejected"
    assert result.content is None
    assert result.quality_decision.action == "reject"
    assert result.repaired is True
    assert result.ai_calls_used == 2


def test_preflight_invalid_work_item_type():
    """work_item exact SocialContentWorkItem değilse 0 AI çağrısı ile content_input_invalid fırlatılır."""
    fake_ai = FakeAIService()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item={"idea_id": 1},  # type: ignore
        )

    assert exc_info.value.reason_code == CONTENT_INPUT_INVALID
    assert exc_info.value.ai_calls_used == 0
    assert fake_ai.call_count == 0


def test_preflight_invalid_prompt_input():
    """prompt_input geçersiz olduğunda 0 AI çağrısı ile content_input_invalid fırlatılır."""
    fake_ai = FakeAIService()
    # Negatif idea_id
    invalid_prompt_input = _make_prompt_input(idea_id=-1)
    work_item = _make_work_item(prompt_input=invalid_prompt_input, idea_id=-1)

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_INPUT_INVALID
    assert exc_info.value.ai_calls_used == 0
    assert fake_ai.call_count == 0


def test_preflight_grounding_prompt_parity_mismatch():
    """Grounding context ile prompt input paritesi uyuşmadığında 0 AI çağrısı ile hata verilir."""
    fake_ai = FakeAIService()
    prompt_input = _make_prompt_input(product_facts="Orijinal bilgi")
    # Farklı product_facts içeren grounding context
    mismatched_context = SocialContentGroundingContext(
        primary_keyword=prompt_input.primary_keyword.keyword,
        product_facts="Farklı bilgi",
        trusted_brand_usp=prompt_input.trusted_brand_usp,
    )
    work_item = SocialContentWorkItem(
        idea_id=prompt_input.idea_id,
        prompt_input=prompt_input,
        grounding_context=mismatched_context,
    )

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_INPUT_INVALID
    assert exc_info.value.ai_calls_used == 0
    assert fake_ai.call_count == 0


def test_preflight_idea_id_mismatch():
    """work_item.idea_id ile prompt_input.idea_id uyuşmadığında 0 AI çağrısı ile hata verilir."""
    fake_ai = FakeAIService()
    prompt_input = _make_prompt_input(idea_id=20)
    work_item = _make_work_item(prompt_input=prompt_input, idea_id=99)

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_INPUT_INVALID
    assert exc_info.value.ai_calls_used == 0
    assert fake_ai.call_count == 0


def test_preflight_none_ai_service():
    """ai_service None olduğunda 0 AI çağrısı ile hata verilir."""
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=None,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_INPUT_INVALID
    assert exc_info.value.ai_calls_used == 0


def test_initial_generation_provider_error():
    """İlk üretimde sağlayıcı hatası olduğunda 1 AI çağrısı ile content_provider_error verilir."""
    fake_ai = FakeAIService(raise_exc=RuntimeError("Google Gemini API unavailable"))
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_PROVIDER_ERROR
    assert exc_info.value.ai_calls_used == 1
    assert fake_ai.call_count == 1
    # Hassas sağlayıcı mesajı dışarı sızmamalı
    assert "Google Gemini" not in str(exc_info.value)


def test_initial_generation_output_invalid():
    """İlk üretimde şema veya sözleşme ihlali olduğunda 1 AI çağrısı ile content_output_invalid verilir."""
    broken_resp = {"hooks": []}  # hooks en az 1 eleman olmalı
    fake_ai = FakeAIService([json.dumps(broken_resp)])
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_OUTPUT_INVALID
    assert exc_info.value.ai_calls_used == 1
    assert fake_ai.call_count == 1


def test_repair_provider_error():
    """Repair sırasında sağlayıcı hatası olduğunda 2 AI çağrısı ile content_repair_provider_error verilir."""
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="En iyi sonuç için 100 bin kişi gibi siz de deneyin.",
    )
    fake_ai = FakeAIService([
        json.dumps(first_resp),
        RuntimeError("Network timeout during repair call"),
    ])
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_REPAIR_PROVIDER_ERROR
    assert exc_info.value.ai_calls_used == 2
    assert fake_ai.call_count == 2
    assert "Network timeout" not in str(exc_info.value)


def test_repair_output_invalid():
    """Repair sırasında çıktı doğrulanamadığında 2 AI çağrısı ile content_repair_output_invalid verilir."""
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="En iyi sonuç için 100 bin kişi gibi siz de deneyin.",
    )
    broken_repair_resp = {"hooks": "invalid"}  # liste değil
    fake_ai = FakeAIService([
        json.dumps(first_resp),
        json.dumps(broken_repair_resp),
    ])
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc_info:
        execute_social_content_work_item(
            ai_service=fake_ai,
            work_item=work_item,
        )

    assert exc_info.value.reason_code == CONTENT_REPAIR_OUTPUT_INVALID
    assert exc_info.value.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_forged_generator_quality_decision_detected():
    """repair_once tarafından döndürülen sahte veya uyuşmayan karar fail-closed engellenir."""
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="%80 başarı sağlayan formül!",
    )
    # 2. yanıtta iddia hâlâ var ("yüzde 100 garanti")
    second_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="yüzde 100 garanti!",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    # Generator repair_once'ı monkeypatch ederek sahte 'accept' kararı dönecek şekilde simüle et
    original_repair_once = SocialBriefContentGenerator.repair_once

    def fake_repair_once(self, prompt_input, original_content, grounding_context):
        real_result = original_repair_once(self, prompt_input, original_content, grounding_context)
        # Gerçekte reject olan kararı hileli accept kararı ile değiştir
        forged_decision = SocialContentQualityDecision(
            action="accept",
            reason_codes=(),
            claims=(),
            warnings=(),
            grounding_clean=True,
            duration_acceptable=True,
            repair_attempted=True,
        )
        return SocialContentRepairAIResult(
            attempt_id=real_result.attempt_id,
            idea_id=real_result.idea_id,
            content=real_result.content,
            quality_decision=forged_decision,
            ai_calls_used=real_result.ai_calls_used,
        )

    with patch.object(SocialBriefContentGenerator, "repair_once", fake_repair_once):
        with pytest.raises(SocialContentItemExecutionError) as exc_info:
            execute_social_content_work_item(
                ai_service=fake_ai,
                work_item=work_item,
            )

    assert exc_info.value.reason_code == CONTENT_EXECUTION_INCONSISTENT
    assert exc_info.value.ai_calls_used == 2


def test_second_repair_request_strictly_forbidden():
    """Post-repair sonrasında kalite kararı 'repair' gelirse fail-closed durulur (2. repair yasak)."""
    first_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="%80 başarı sağlayan formül!",
    )
    second_resp = _make_valid_video_response_dict(
        end_sec=24,
        caption="Temiz içerik.",
    )
    fake_ai = FakeAIService([json.dumps(first_resp), json.dumps(second_resp)])
    work_item = _make_work_item()

    # evaluate_social_content_quality repair_attempted=True iken sahte 'repair' kararı üretirse
    original_eval = evaluate_social_content_quality

    def fake_eval(content, context, *, repair_attempted):
        real_dec = original_eval(content, context, repair_attempted=repair_attempted)
        if repair_attempted:
            return SocialContentQualityDecision(
                action="repair",
                reason_codes=("ungrounded_claim",),
                claims=("claim",),
                warnings=(),
                grounding_clean=False,
                duration_acceptable=True,
                repair_attempted=True,
            )
        return real_dec

    with patch(
        "app.core.social.content_item_execution.evaluate_social_content_quality",
        side_effect=fake_eval,
    ):
        with patch.object(
            SocialBriefContentGenerator,
            "repair_once",
        ) as mock_repair:
            # generator da uyumlu karar döndürsün
            valid_content = ValidatedSocialContent(
                hooks=(ValidatedHook(text="Hook", style="curiosity"),),
                caption="Temiz",
                format_payload=None,
                visual_suggestion=None,
                video_concept=None,
                cta_text="Tıkla",
                hashtags=("etiket1", "etiket2", "etiket3", "etiket4", "etiket5"),
                industry_posting_suggestion=None,
                platform_notes=None,
                duration_status="not_applicable",
                actual_duration_sec=None,
                validation_warnings=(),
                scenario=None,
            )
            mock_repair.return_value = SocialContentRepairAIResult(
                attempt_id=10,
                idea_id=20,
                content=valid_content,
                quality_decision=SocialContentQualityDecision(
                    action="repair",
                    reason_codes=("ungrounded_claim",),
                    claims=("claim",),
                    warnings=(),
                    grounding_clean=False,
                    duration_acceptable=True,
                    repair_attempted=True,
                ),
                ai_calls_used=1,
            )
            with pytest.raises(SocialContentItemExecutionError) as exc_info:
                execute_social_content_work_item(
                    ai_service=fake_ai,
                    work_item=work_item,
                )

    assert exc_info.value.reason_code == CONTENT_EXECUTION_INCONSISTENT
    assert exc_info.value.ai_calls_used == 2


def test_result_dto_immutability_and_invariants():
    """SocialContentItemExecutionResult DTO'su dondurulmuştur ve sözleşme kurallarını korur."""
    valid_content = ValidatedSocialContent(
        hooks=(ValidatedHook(text="Hook", style="curiosity"),),
        caption="Temiz içerik",
        format_payload=None,
        visual_suggestion=None,
        video_concept=None,
        cta_text="Tıkla",
        hashtags=("etiket1", "etiket2", "etiket3", "etiket4", "etiket5"),
        industry_posting_suggestion=None,
        platform_notes=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=(),
        scenario=None,
    )
    accept_decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )

    result = SocialContentItemExecutionResult(
        attempt_id=10,
        idea_id=20,
        status="accepted",
        content=valid_content,
        quality_decision=accept_decision,
        repaired=False,
        ai_calls_used=1,
    )

    # 1. Immutability
    with pytest.raises(FrozenInstanceError):
        result.status = "rejected"  # type: ignore

    # 2. accepted durumunda content=None olamaz
    with pytest.raises(SocialContentItemExecutionError) as exc:
        SocialContentItemExecutionResult(
            attempt_id=10,
            idea_id=20,
            status="accepted",
            content=None,
            quality_decision=accept_decision,
            repaired=False,
            ai_calls_used=1,
        )
    assert exc.value.reason_code == CONTENT_EXECUTION_INCONSISTENT

    # 3. rejected durumunda content dolu olamaz
    reject_decision = SocialContentQualityDecision(
        action="reject",
        reason_codes=("ungrounded_claim",),
        claims=("iddia",),
        warnings=(),
        grounding_clean=False,
        duration_acceptable=True,
        repair_attempted=True,
    )
    with pytest.raises(SocialContentItemExecutionError) as exc:
        SocialContentItemExecutionResult(
            attempt_id=10,
            idea_id=20,
            status="rejected",
            content=valid_content,
            quality_decision=reject_decision,
            repaired=True,
            ai_calls_used=2,
        )
    assert exc.value.reason_code == CONTENT_EXECUTION_INCONSISTENT

    # 4. repaired=False iken ai_calls_used=2 olamaz
    with pytest.raises(SocialContentItemExecutionError) as exc:
        SocialContentItemExecutionResult(
            attempt_id=10,
            idea_id=20,
            status="accepted",
            content=valid_content,
            quality_decision=accept_decision,
            repaired=False,
            ai_calls_used=2,
        )
    assert exc.value.reason_code == CONTENT_EXECUTION_INCONSISTENT

    # 5. repaired=True iken ai_calls_used=1 olamaz
    repaired_accept_decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=True,
    )
    with pytest.raises(SocialContentItemExecutionError) as exc:
        SocialContentItemExecutionResult(
            attempt_id=10,
            idea_id=20,
            status="accepted",
            content=valid_content,
            quality_decision=repaired_accept_decision,
            repaired=True,
            ai_calls_used=1,
        )
    assert exc.value.reason_code == CONTENT_EXECUTION_INCONSISTENT


def test_error_sanitization_no_leak():
    """SocialContentItemExecutionError hiçbir ham prompt, caption veya hassas veri sızdırmaz."""
    sensitive_caption = "Kullanıcıya özel gizli bilgi ve formül!"
    sensitive_claim = "Kesin kazanç garantisi!"

    err = SocialContentItemExecutionError(
        "İçerik üretimi sırasında hata oluştu.",
        error_code="CONTENT_ITEM_EXECUTION_FAILED",
        reason_code=CONTENT_PROVIDER_ERROR,
        ai_calls_used=1,
    )

    err_str = str(err)
    assert sensitive_caption not in err_str
    assert sensitive_claim not in err_str
    assert "CONTENT_ITEM_EXECUTION_FAILED" in err_str
    assert "content_provider_error" in err_str
    assert "(ai_calls_used=1)" in err_str


def test_format_parity_post_format():
    """Statik post formatında da clean generate -> accept akışı tam pariteyle çalışır."""
    post_spec = ContentTargetSpec(
        target_id=2,
        platform="instagram",
        content_format="post",
        duration_preset_id=None,
        duration_min_sec=None,
        duration_max_sec=None,
    )
    prompt_input = _make_prompt_input(target_spec=post_spec)
    work_item = _make_work_item(prompt_input=prompt_input)

    post_resp = _make_valid_post_response_dict()
    fake_ai = FakeAIService([json.dumps(post_resp)])

    result = execute_social_content_work_item(
        ai_service=fake_ai,
        work_item=work_item,
    )

    assert result.status == "accepted"
    assert result.content is not None
    assert result.content.format_payload is None
    assert result.content.duration_status == "not_applicable"
    assert result.repaired is False
    assert result.ai_calls_used == 1
    assert fake_ai.call_count == 1
    assert fake_ai.call_args[0]["temperature"] is None


# ==================== SoftTimeLimitExceeded + K8 (defect fix testleri) ====================


def test_soft_time_limit_exceeded_not_swallowed_during_initial_generation():
    """SoftTimeLimitExceeded ilk üretim sırasında sıradan bir sağlayıcı hatasına ÇEVRİLMEZ.

    Celery'nin billiard.exceptions.SoftTimeLimitExceeded'i (Exception alt sınıfı)
    generate() ve execute_social_content_work_item'daki broad except bloklarından
    olduğu gibi yukarı fırlatılmalı; content_provider_error warning'ine
    dönüştürülüp yutulmamalıdır (CLAUDE.md §11).
    """
    from celery.exceptions import SoftTimeLimitExceeded

    fake_ai = FakeAIService(raise_exc=SoftTimeLimitExceeded("soft time limit"))
    work_item = _make_work_item()

    with pytest.raises(SoftTimeLimitExceeded):
        execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)


def test_soft_time_limit_exceeded_not_swallowed_during_repair():
    """SoftTimeLimitExceeded repair_once sırasında da olduğu gibi yukarı fırlatılır."""
    from celery.exceptions import SoftTimeLimitExceeded

    # 1. çağrı: süre uyumsuz ama grounding temiz -> repair tetiklenir.
    first_resp = _make_valid_video_response_dict(end_sec=10)
    fake_ai = FakeAIService([json.dumps(first_resp)])
    original_complete_json = fake_ai.complete_json
    call_counter = {"n": 0}

    def complete_json_side_effect(prompt: str, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return original_complete_json(prompt, **kwargs)
        raise SoftTimeLimitExceeded("soft time limit during repair")

    fake_ai.complete_json = complete_json_side_effect
    work_item = _make_work_item()

    with pytest.raises(SoftTimeLimitExceeded):
        execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)


def test_duration_only_repair_provider_error_saved_with_mismatch():
    """K8 (plan_social_brief_akisi.md §4): süre-tek-sorunlu içerikte repair sağlayıcı
    hatasıyla başarısız olursa, orijinal grounding-temiz içerik duration_status=mismatch
    ile ACCEPTED döner (tamamen kaybedilmez), ai_calls_used=2, repaired=True.
    """
    # 1. çağrı: 10 sn (hedef aralık 16-30 -> mismatch), grounding temiz.
    first_resp = _make_valid_video_response_dict(end_sec=10)
    fake_ai = FakeAIService([
        json.dumps(first_resp),
        RuntimeError("Gemini 503 sağlayıcı hatası"),
    ])
    work_item = _make_work_item()

    result = execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert result.status == "accepted"
    assert result.content is not None
    assert result.content.actual_duration_sec == 10
    assert result.content.duration_status == "mismatch"
    assert result.quality_decision.action == "accept"
    assert "duration_mismatch" in result.quality_decision.warnings
    assert result.repaired is True
    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_duration_only_repair_output_invalid_saved_with_mismatch():
    """K8: repair_once geçersiz çıktı üretirse (CONTENT_REPAIR_OUTPUT_INVALID) de
    aynı kurtarma davranışı uygulanır: orijinal içerik mismatch ile accepted döner.
    """
    first_resp = _make_valid_video_response_dict(end_sec=10)
    # 2. çağrı: zorunlu alan eksik -> CONTENT_REPAIR_OUTPUT_INVALID
    invalid_repair_resp = json.dumps({"caption": "eksik alanlar"})
    fake_ai = FakeAIService([json.dumps(first_resp), invalid_repair_resp])
    work_item = _make_work_item()

    result = execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert result.status == "accepted"
    assert result.content.duration_status == "mismatch"
    assert result.repaired is True
    assert result.ai_calls_used == 2


def test_claim_and_duration_repair_provider_error_still_fails():
    """Grounding sorunu da varsa (yalnız süre değil), repair provider error'da
    K8 kurtarması UYGULANMAZ; mevcut fail-closed davranış (rejected/exception) korunur.
    """
    # 1. çağrı: hem süre mismatch (10s) hem ungrounded claim.
    first_resp = _make_valid_video_response_dict(
        end_sec=10,
        caption="50 bin kullanıcı yanılıyor olamaz!",
    )
    fake_ai = FakeAIService([
        json.dumps(first_resp),
        RuntimeError("Gemini 503 sağlayıcı hatası"),
    ])
    work_item = _make_work_item()

    with pytest.raises(SocialContentItemExecutionError) as exc:
        execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert exc.value.reason_code == CONTENT_REPAIR_PROVIDER_ERROR
    assert exc.value.ai_calls_used == 2
