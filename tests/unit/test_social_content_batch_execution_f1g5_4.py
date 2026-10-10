# -*- coding: utf-8 -*-
"""Unit tests for DB'siz Çoklu Work Item Batch Yürütücüsü ve Sonuç Agregasyonu (F1-G.5.4).

Bu test süiti:
1. Tüm item'lar ilk üretimde accepted -> completed
2. Bir item repair sonrası accepted -> completed
3. Bir accepted, bir rejected -> partial
4. Bir accepted, bir provider error -> partial
5. Tüm item'lar rejected/error -> failed
6. already_present + accepted -> completed
7. already_present + rejected -> partial
8. Tüm requested fikirler already_present -> completed ve 0 AI
9. completed replay -> 0 AI, replayed=True
10. Deterministik requested/work-item/result/warning sırası
11. Aynı item için maksimum iki çağrı
12. Batch toplamı 2 × work item sayısını aşamaz
13. Bir item hata verdiğinde sonraki item yine çalışır
14. Rejected içerik accepted_results içine giremez
15. Rejected warning claims içerir
16. Provider/output warning claims=() içerir ve hassas mesaj sızdırmaz
17. Duplicate veya coverage dışı work item reddi, 0 AI
18. already_present ile work item kesişimi reddi, 0 AI
19. Eksik partition/coverage reddi, 0 AI
20. Kanonik sırayı bozan input reddi, 0 AI
21. Result DTO invariant ve deep immutability testleri
22. Fake AI çağrılarında temperature=None
23. Modülde DB, ORM, Session, persistence, heartbeat ve finish_attempt importu olmadığının testi
24. CONTENT_PROMPT_INVALID_INPUT/TARGET hatasında item execution ai_calls_used=0
25. Repair input/prompt hatasında toplam ai_calls_used=1
26. Beklenmeyen item exception'ının güvenli warning'e dönüştürülmesi
27. Ham prompt/caption/provider mesajının str/repr/warnings içinde bulunmaması
senaryolarını izole sahte AI servisi ile doğrular.
"""
from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import patch

import pytest

from app.core.social.content_batch_execution import (
    BATCH_WARNING_REASON_ALLOWLIST,
    SocialContentBatchExecutionError,
    SocialContentBatchExecutionResult,
    SocialContentBatchWarning,
    execute_social_content_batch,
)
from app.core.social.content_contract import (
    ContentTargetSpec,
    ValidatedHook,
    ValidatedSocialContent,
    validate_social_content_output,
)
from app.core.social.content_item_execution import (
    SocialContentItemExecutionError,
    SocialContentItemExecutionResult,
    execute_social_content_work_item,
)
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    SocialContentQualityDecision,
)
from app.core.social.content_worker_input import (
    SocialContentWorkerPreparation,
    SocialContentWorkItem,
)
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentGenerationError,
    SocialContentRepairError,
)
from app.generators.social.brief_content_prompt import (
    SocialContentKeywordInput,
    SocialContentPromptInput,
)


# ==================== TEST YARDIMCILARI & FAKE AI ====================


class FakeCollector:
    def __init__(self) -> None:
        self.failed_reasons: list[str] = []

    def mark_current_attempt_failed(self, reason: str) -> bool:
        self.failed_reasons.append(reason)
        return True

    def logical_request(self):
        from contextlib import nullcontext
        return nullcontext()


class FakeAIService:
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
    idea_id: int = 101,
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
    *,
    attempt_id: int = 10,
    idea_id: int = 101,
    prompt_input: SocialContentPromptInput | None = None,
    grounding_context: SocialContentGroundingContext | None = None,
) -> SocialContentWorkItem:
    if prompt_input is None:
        prompt_input = _make_prompt_input(attempt_id=attempt_id, idea_id=idea_id)
    if grounding_context is None:
        grounding_context = SocialContentGroundingContext(
            primary_keyword=prompt_input.primary_keyword.keyword,
            product_facts=prompt_input.product_facts,
            trusted_brand_usp=prompt_input.trusted_brand_usp,
        )
    return SocialContentWorkItem(
        idea_id=idea_id,
        prompt_input=prompt_input,
        grounding_context=grounding_context,
    )


def _make_preparation(
    *,
    attempt_id: int = 10,
    task_id: str = "task-uuid-1",
    brief_id: int = 1,
    scoring_run_id: int = 2,
    requested_idea_ids: tuple[int, ...] = (101, 102),
    already_present_idea_ids: tuple[int, ...] = (),
    already_completed: bool = False,
    work_items: tuple[SocialContentWorkItem, ...] | None = None,
) -> SocialContentWorkerPreparation:
    if work_items is None:
        items_list = []
        for i_id in requested_idea_ids:
            if i_id not in already_present_idea_ids:
                items_list.append(_make_work_item(attempt_id=attempt_id, idea_id=i_id))
        work_items = tuple(items_list)

    return SocialContentWorkerPreparation(
        attempt_id=attempt_id,
        task_id=task_id,
        brief_id=brief_id,
        scoring_run_id=scoring_run_id,
        requested_idea_ids=requested_idea_ids,
        already_present_idea_ids=already_present_idea_ids,
        already_completed=already_completed,
        work_items=work_items,
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


# ==================== TESTLER ====================


def test_01_all_items_accepted_first_try_completed():
    """Tüm item'lar ilk üretimde accepted olduğunda batch status 'completed' döner."""
    resp1 = _make_valid_video_response_dict(caption="1. video açıklaması.")
    resp2 = _make_valid_video_response_dict(caption="2. video açıklaması.")
    fake_ai = FakeAIService([json.dumps(resp1), json.dumps(resp2)])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert len(result.accepted_results) == 2
    assert result.accepted_idea_ids == (101, 102)
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert result.ai_calls_used == 2
    assert result.replayed is False
    assert fake_ai.call_count == 2


def test_02_one_item_accepted_after_repair_completed():
    """Bir item tek repair sonrası accepted olduğunda batch status 'completed' döner."""
    # Item 101: 1st response iddia (%80), 2nd response temiz
    resp101_dirty = _make_valid_video_response_dict(caption="%80 başarı sağlayan formül!")
    resp101_clean = _make_valid_video_response_dict(caption="Doğal adımlarla cildinize özen gösterin.")
    # Item 102: ilk seferde temiz
    resp102_clean = _make_valid_video_response_dict(caption="2. temiz video metni.")

    fake_ai = FakeAIService([
        json.dumps(resp101_dirty),
        json.dumps(resp101_clean),
        json.dumps(resp102_clean),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert len(result.accepted_results) == 2
    assert result.accepted_idea_ids == (101, 102)
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert result.ai_calls_used == 3  # 101: 2 çağrı, 102: 1 çağrı
    assert fake_ai.call_count == 3


def test_03_one_accepted_one_rejected_partial():
    """Bir accepted, bir rejected olduğunda batch status 'partial' döner ve rejected warning oluşur."""
    resp101_clean = _make_valid_video_response_dict(caption="101 temiz video.")
    resp102_dirty1 = _make_valid_video_response_dict(caption="%80 başarı sağlayan formül!")
    resp102_dirty2 = _make_valid_video_response_dict(caption="Yine de %80 başarı formülü!")

    fake_ai = FakeAIService([
        json.dumps(resp101_clean),
        json.dumps(resp102_dirty1),
        json.dumps(resp102_dirty2),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert len(result.accepted_results) == 1
    assert result.accepted_results[0].idea_id == 101
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 102
    assert result.warnings[0].reason_code == "content_rejected"
    assert len(result.warnings[0].claims) > 0
    assert result.warnings[0].ai_calls_used == 2
    assert result.ai_calls_used == 3  # 101: 1, 102: 2


def test_04_one_accepted_one_provider_error_partial():
    """Bir accepted, bir provider error olduğunda batch status 'partial' döner."""
    resp101_clean = _make_valid_video_response_dict(caption="101 temiz video.")
    fake_ai = FakeAIService([
        json.dumps(resp101_clean),
        RuntimeError("Gemini server 503 error"),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 102
    assert result.warnings[0].reason_code == "content_provider_error"
    assert result.warnings[0].claims == ()
    assert result.warnings[0].ai_calls_used == 1
    assert result.ai_calls_used == 2


def test_05_all_items_rejected_or_error_failed():
    """Tüm item'lar başarısız olduğunda batch status 'failed' döner."""
    resp101_dirty1 = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    resp101_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı formülü!")
    fake_ai = FakeAIService([
        json.dumps(resp101_dirty1),
        json.dumps(resp101_dirty2),
        RuntimeError("Provider failure"),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "failed"
    assert result.accepted_results == ()
    assert result.accepted_idea_ids == ()
    assert result.unresolved_idea_ids == (101, 102)
    assert len(result.warnings) == 2
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_rejected"
    assert result.warnings[1].idea_id == 102
    assert result.warnings[1].reason_code == "content_provider_error"


def test_06_already_present_plus_accepted_completed():
    """already_present ve accepted birleşimi tüm requested kümesini kaplarsa completed döner."""
    resp102_clean = _make_valid_video_response_dict(caption="102 temiz video.")
    fake_ai = FakeAIService([json.dumps(resp102_clean)])

    prep = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101,),
    )
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert result.already_present_idea_ids == (101,)
    assert result.accepted_idea_ids == (102,)
    assert len(result.accepted_results) == 1
    assert result.accepted_results[0].idea_id == 102
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert result.ai_calls_used == 1
    assert fake_ai.call_count == 1


def test_07_already_present_plus_rejected_partial():
    """already_present mevcutken work item rejected olursa batch partial döner."""
    resp102_dirty1 = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    resp102_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı formülü!")
    fake_ai = FakeAIService([
        json.dumps(resp102_dirty1),
        json.dumps(resp102_dirty2),
    ])

    prep = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101,),
    )
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.already_present_idea_ids == (101,)
    assert result.accepted_idea_ids == ()
    assert result.accepted_results == ()
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 102
    assert result.warnings[0].reason_code == "content_rejected"
    assert result.ai_calls_used == 2


def test_08_all_requested_already_present_completed_zero_ai():
    """Tüm requested fikirler already_present ise 0 AI çağrısıyla completed döner."""
    fake_ai = FakeAIService()

    prep = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101, 102),
        work_items=(),
        already_completed=False,
    )
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert result.already_present_idea_ids == (101, 102)
    assert result.accepted_results == ()
    assert result.accepted_idea_ids == ()
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert result.ai_calls_used == 0
    assert result.replayed is False
    assert fake_ai.call_count == 0


def test_09_completed_replay_zero_ai():
    """already_completed=True durumunda doğrudan 0 AI çağrısıyla replayed=True döner."""
    fake_ai = FakeAIService()

    prep = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101, 102),
        work_items=(),
        already_completed=True,
    )
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert result.replayed is True
    assert result.ai_calls_used == 0
    assert result.accepted_results == ()
    assert result.accepted_idea_ids == ()
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert fake_ai.call_count == 0


def test_10_deterministic_ordering_preserved():
    """Kanonik requested_idea_ids sırası sonuçlarda ve uyarılarda harfiyen korunur."""
    requested = (103, 101, 105, 102)
    already_present = (103, 105)
    # work_items kanonik sırada: (101, 102)
    item101 = _make_work_item(idea_id=101)
    item102 = _make_work_item(idea_id=102)

    resp101 = _make_valid_video_response_dict(caption="101 kabul")
    # 102 provider error
    fake_ai = FakeAIService([json.dumps(resp101), RuntimeError("Error on 102")])

    prep = _make_preparation(
        requested_idea_ids=requested,
        already_present_idea_ids=already_present,
        work_items=(item101, item102),
    )
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.requested_idea_ids == (103, 101, 105, 102)
    assert result.already_present_idea_ids == (103, 105)
    assert result.accepted_idea_ids == (101,)
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 102


def test_11_max_two_ai_calls_per_item():
    """Herhangi bir work item için en fazla iki AI çağrısı yapılabilir."""
    resp_dirty1 = _make_valid_video_response_dict(caption="%80 başarı!")
    resp_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı!")
    fake_ai = FakeAIService([json.dumps(resp_dirty1), json.dumps(resp_dirty2)])

    prep = _make_preparation(requested_idea_ids=(101,))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.ai_calls_used == 2
    assert fake_ai.call_count == 2


def test_12_batch_total_calls_ceiling():
    """Batch genelinde toplam çağrı sayısı 2 × work_items sayısını kesinlikle aşamaz."""
    resp1_d = _make_valid_video_response_dict(caption="%80 başarı!")
    resp1_c = _make_valid_video_response_dict(caption="Temiz 1")
    resp2_d = _make_valid_video_response_dict(caption="%80 başarı!")
    resp2_c = _make_valid_video_response_dict(caption="Temiz 2")

    fake_ai = FakeAIService([
        json.dumps(resp1_d), json.dumps(resp1_c),
        json.dumps(resp2_d), json.dumps(resp2_c),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.ai_calls_used <= 2 * len(prep.work_items)
    assert result.ai_calls_used == 4


def test_13_item_error_does_not_halt_batch():
    """İlk item hata verse bile batch durmaz, sonraki item'ları başarıyla yürütür."""
    # 101: output invalid
    broken_resp = {"hooks": []}
    # 102: clean accept
    valid_resp = _make_valid_video_response_dict(caption="102 temiz")

    fake_ai = FakeAIService([json.dumps(broken_resp), json.dumps(valid_resp)])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (102,)
    assert result.unresolved_idea_ids == (101,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_output_invalid"


def test_14_rejected_content_never_in_accepted_results():
    """Reddedilen içerik nesnesi accepted_results içine kesinlikle giremez."""
    resp_dirty1 = _make_valid_video_response_dict(caption="%80 başarı!")
    resp_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı!")
    fake_ai = FakeAIService([json.dumps(resp_dirty1), json.dumps(resp_dirty2)])

    prep = _make_preparation(requested_idea_ids=(101,))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "failed"
    assert result.accepted_results == ()
    assert result.accepted_idea_ids == ()


def test_15_rejected_warning_contains_claims():
    """Kalite nedeniyle reddedilen uyarının claims alanı otoriter iddiaları içerir."""
    resp_dirty1 = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    resp_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı formülü!")
    fake_ai = FakeAIService([json.dumps(resp_dirty1), json.dumps(resp_dirty2)])

    prep = _make_preparation(requested_idea_ids=(101,))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.reason_code == "content_rejected"
    assert len(w.claims) > 0
    assert any("%80" in c for c in w.claims)


def test_16_provider_warning_claims_empty_no_leak():
    """Provider/output hatası uyarısında claims boştur ve hassas mesaj bulunmaz."""
    fake_ai = FakeAIService(raise_exc=RuntimeError("Secret database credentials leaked!"))

    prep = _make_preparation(requested_idea_ids=(101,))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.reason_code == "content_provider_error"
    assert w.claims == ()
    assert "Secret" not in str(w)
    assert "credentials" not in str(w)


def test_17_preflight_duplicate_or_out_of_bounds_work_item_rejected():
    """work_items içinde duplicate veya requested dışı ID varsa 0 AI çağrısıyla reddedilir."""
    fake_ai = FakeAIService()
    item1 = _make_work_item(idea_id=101)
    item2 = _make_work_item(idea_id=101)  # Duplicate

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        prep = _make_preparation(
            requested_idea_ids=(101, 102),
            work_items=(item1, item2),
        )
        execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert exc_info.value.field == "work_items"
    assert fake_ai.call_count == 0


def test_18_preflight_already_present_and_work_item_intersection_rejected():
    """already_present ile work_items kesişirse 0 AI çağrısıyla reddedilir."""
    fake_ai = FakeAIService()
    item101 = _make_work_item(idea_id=101)

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        prep = _make_preparation(
            requested_idea_ids=(101, 102),
            already_present_idea_ids=(101,),
            work_items=(item101,),  # 101 her ikisinde de var!
        )
        execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert exc_info.value.field == "work_items"
    assert fake_ai.call_count == 0


def test_19_preflight_missing_partition_coverage_rejected():
    """already_present ve work_items requested kümesini eksik kapsarsa reddedilir."""
    fake_ai = FakeAIService()
    item101 = _make_work_item(idea_id=101)
    # 102 ne already_present içinde ne work_items içinde!

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        prep = _make_preparation(
            requested_idea_ids=(101, 102),
            already_present_idea_ids=(),
            work_items=(item101,),
        )
        execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert exc_info.value.field == "work_items"
    assert fake_ai.call_count == 0


def test_20_preflight_non_canonical_order_rejected():
    """work_items sırası requested sırasını bozarsa 0 AI çağrısıyla reddedilir."""
    fake_ai = FakeAIService()
    item101 = _make_work_item(idea_id=101)
    item102 = _make_work_item(idea_id=102)

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        # requested (101, 102) ama items (102, 101)
        prep = _make_preparation(
            requested_idea_ids=(101, 102),
            already_present_idea_ids=(),
            work_items=(item102, item101),
        )
        execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert exc_info.value.field == "work_items"
    assert fake_ai.call_count == 0


def test_21_result_dto_invariant_and_immutability():
    """Result ve Warning DTO'ları dondurulmuştur ve invariant denetimleri çalışır."""
    warning = SocialContentBatchWarning(
        idea_id=101,
        reason_code="content_rejected",
        claims=("%80",),
        ai_calls_used=2,
    )
    with pytest.raises(FrozenInstanceError):
        warning.reason_code = "content_provider_error"  # type: ignore

    res = SocialContentBatchExecutionResult(
        attempt_id=10,
        brief_id=1,
        scoring_run_id=2,
        status="completed",
        requested_idea_ids=(101,),
        already_present_idea_ids=(101,),
        accepted_results=(),
        accepted_idea_ids=(),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=0,
        replayed=True,
    )
    with pytest.raises(FrozenInstanceError):
        res.status = "failed"  # type: ignore

    # status completed iken unresolved_idea_ids dolu olamaz
    with pytest.raises(SocialContentBatchExecutionError):
        SocialContentBatchExecutionResult(
            attempt_id=10,
            brief_id=1,
            scoring_run_id=2,
            status="completed",
            requested_idea_ids=(101,),
            already_present_idea_ids=(),
            accepted_results=(),
            accepted_idea_ids=(),
            unresolved_idea_ids=(101,),
            warnings=(warning,),
            ai_calls_used=2,
            replayed=False,
        )


def test_22_temperature_none_across_all_calls():
    """Tüm AI çağrılarında temperature parametresinin None olduğu doğrulanır."""
    resp = _make_valid_video_response_dict()
    fake_ai = FakeAIService([json.dumps(resp)])

    prep = _make_preparation(requested_idea_ids=(101,))
    execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert len(fake_ai.call_args) == 1
    assert fake_ai.call_args[0]["temperature"] is None


def test_23_no_forbidden_imports_in_module():
    """app.core.social.content_batch_execution içinde DB/ORM/Session/Persistence importu yoktur."""
    import app.core.social.content_batch_execution as mod
    import inspect

    src = inspect.getsource(mod)
    tree = ast.parse(src)

    forbidden_names = {
        "sqlalchemy",
        "Session",
        "sessionmaker",
        "database",
        "persist_social_content",
        "heartbeat",
        "finish_attempt",
        "claim_attempt",
        "celery",
    }

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)
            for alias in node.names:
                imported_names.add(alias.name)

    inter = imported_names & forbidden_names
    assert not inter, f"Yasaklı importlar bulundu: {inter}"


def test_24_content_prompt_invalid_input_target_item_calls_zero():
    """CONTENT_PROMPT_INVALID_INPUT veya TARGET hatasında ai_calls_used=0 olarak raporlanır."""
    work_item = _make_work_item(idea_id=101)
    fake_ai = FakeAIService()

    # Generator.generate'ın CONTENT_PROMPT_INVALID_INPUT hatası fırlatmasını simüle et
    with patch.object(
        SocialBriefContentGenerator,
        "generate",
        side_effect=SocialContentGenerationError(
            "Prompt girdi hatası.",
            error_code="CONTENT_PROMPT_INVALID_INPUT",
        ),
    ):
        with pytest.raises(SocialContentItemExecutionError) as exc_info:
            execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert exc_info.value.reason_code == "content_input_invalid"
    assert exc_info.value.ai_calls_used == 0

    # Aynı şekilde TARGET hatası
    with patch.object(
        SocialBriefContentGenerator,
        "generate",
        side_effect=SocialContentGenerationError(
            "Prompt hedef hatası.",
            error_code="CONTENT_PROMPT_INVALID_TARGET",
        ),
    ):
        with pytest.raises(SocialContentItemExecutionError) as exc_info:
            execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert exc_info.value.reason_code == "content_input_invalid"
    assert exc_info.value.ai_calls_used == 0


def test_25_repair_input_prompt_error_item_calls_one():
    """Repair tarafında AI öncesi hata oluştuğunda toplam ai_calls_used=1 kalır."""
    work_item = _make_work_item(idea_id=101)
    # İlk çağrı ungrounded claim ile repair tetikler
    resp_dirty = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    fake_ai = FakeAIService([json.dumps(resp_dirty)])

    # repair_once'ın CONTENT_REPAIR_INVALID_INPUT veya PROMPT_ERROR vermesini simüle et
    with patch.object(
        SocialBriefContentGenerator,
        "repair_once",
        side_effect=SocialContentRepairError(
            "Repair öncesi girdi hatası.",
            error_code="CONTENT_REPAIR_INVALID_INPUT",
        ),
    ):
        with pytest.raises(SocialContentItemExecutionError) as exc_info:
            execute_social_content_work_item(ai_service=fake_ai, work_item=work_item)

    assert exc_info.value.reason_code == "content_repair_input_invalid"
    assert exc_info.value.ai_calls_used == 1

    # Aynı şekilde CONTENT_REPAIR_PROMPT_ERROR
    fake_ai2 = FakeAIService([json.dumps(resp_dirty)])
    with patch.object(
        SocialBriefContentGenerator,
        "repair_once",
        side_effect=SocialContentRepairError(
            "Repair prompt hatası.",
            error_code="CONTENT_REPAIR_PROMPT_ERROR",
        ),
    ):
        with pytest.raises(SocialContentItemExecutionError) as exc_info:
            execute_social_content_work_item(ai_service=fake_ai2, work_item=work_item)

    assert exc_info.value.reason_code == "content_repair_input_invalid"
    assert exc_info.value.ai_calls_used == 1


def test_26_unexpected_item_exception_converted_to_safe_warning():
    """Beklenmeyen bir Python hatası item sınırında yakalanıp güvenli warning'e dönüştürülür."""
    resp101 = _make_valid_video_response_dict(caption="101 temiz")
    fake_ai = FakeAIService([json.dumps(resp101)])

    prep = _make_preparation(requested_idea_ids=(101, 102))

    # 102 çalıştırılırken beklenmeyen TypeError patlasın
    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 102:
            raise TypeError("Beklenmeyen iç Python hatası")
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.idea_id == 102
    assert w.reason_code == "content_execution_inconsistent"
    assert "Beklenmeyen iç Python hatası" not in str(w)


def test_27_no_sensitive_leaks_in_warnings_or_result_strings():
    """Warning ve Result DTO'larının str/repr çıktıları hassas içerik barındırmaz."""
    w = SocialContentBatchWarning(
        idea_id=101,
        reason_code="content_rejected",
        claims=("%80",),
        ai_calls_used=2,
    )
    res = SocialContentBatchExecutionResult(
        attempt_id=10,
        brief_id=1,
        scoring_run_id=2,
        status="completed",
        requested_idea_ids=(101,),
        already_present_idea_ids=(101,),
        accepted_results=(),
        accepted_idea_ids=(),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=0,
        replayed=True,
    )

    w_str = str(w)
    res_str = str(res)

    for sensitive_word in ("prompt", "SELECT", "INSERT", "Bearer", "api_key", "password"):
        assert sensitive_word not in w_str
        assert sensitive_word not in res_str


def _make_dummy_accepted_item_result(
    *,
    attempt_id: int = 10,
    idea_id: int = 101,
    ai_calls_used: int = 1,
) -> SocialContentItemExecutionResult:
    target_spec = ContentTargetSpec(
        target_id=1,
        platform="instagram",
        content_format="reels",
        duration_preset_id="short_16_30",
        duration_min_sec=16,
        duration_max_sec=30,
    )
    raw = _make_valid_video_response_dict()
    content = validate_social_content_output(raw=raw, target_spec=target_spec)
    quality = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    return SocialContentItemExecutionResult(
        attempt_id=attempt_id,
        idea_id=idea_id,
        status="accepted",
        content=content,
        quality_decision=quality,
        repaired=False,
        ai_calls_used=ai_calls_used,
    )


def test_28_forged_accepted_result_wrong_attempt_id():
    """Forged accepted result yanlış attempt_id taşıdığında unresolved kalır ve warning üretilir."""
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp102)])

    prep = _make_preparation(requested_idea_ids=(101, 102))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 101:
            return _make_dummy_accepted_item_result(attempt_id=999, idea_id=101, ai_calls_used=1)
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (102,)
    assert len(result.accepted_results) == 1
    assert result.accepted_results[0].idea_id == 102
    assert result.unresolved_idea_ids == (101,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_execution_inconsistent"
    assert result.warnings[0].claims == ()
    assert result.warnings[0].ai_calls_used == 1


def test_29_forged_accepted_result_wrong_idea_id():
    """Forged accepted result yanlış idea_id taşıdığında KeyError ile crash olmaz, ilgili item unresolved kalır."""
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp102)])

    prep = _make_preparation(requested_idea_ids=(101, 102))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 101:
            return _make_dummy_accepted_item_result(attempt_id=10, idea_id=999, ai_calls_used=1)
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (102,)
    assert result.unresolved_idea_ids == (101,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_execution_inconsistent"
    assert result.warnings[0].claims == ()
    assert result.warnings[0].ai_calls_used == 1


def test_30_forged_rejected_result_wrong_idea_id():
    """Forged rejected result yanlış idea_id taşıdığında content_rejected olarak güvenilmez, inconsistent olur."""
    fake_ai = FakeAIService()
    prep = _make_preparation(requested_idea_ids=(101,))

    quality = SocialContentQualityDecision(
        action="reject",
        reason_codes=("ungrounded_claim",),
        claims=("%80 başarı",),
        warnings=(),
        grounding_clean=False,
        duration_acceptable=True,
        repair_attempted=True,
    )
    forged_rejected = SocialContentItemExecutionResult(
        attempt_id=10,
        idea_id=999,  # Yanlış idea_id
        status="rejected",
        content=None,
        quality_decision=quality,
        repaired=True,
        ai_calls_used=2,
    )

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        return_value=forged_rejected,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "failed"
    assert result.unresolved_idea_ids == (101,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_execution_inconsistent"
    assert result.warnings[0].claims == ()
    assert result.warnings[0].ai_calls_used == 2


def test_31_non_exact_dto_child_return():
    """Child dönüşü exact SocialContentItemExecutionResult olmadığında güvenli warning (ai_calls_used=2) üretilir."""
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp102)])

    prep = _make_preparation(requested_idea_ids=(101, 102))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 101:
            return {"status": "accepted", "idea_id": 101, "ai_calls_used": 1}
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (102,)
    assert result.unresolved_idea_ids == (101,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 101
    assert result.warnings[0].reason_code == "content_execution_inconsistent"
    assert result.warnings[0].claims == ()
    assert result.warnings[0].ai_calls_used == 2


def test_32_unexpected_exception_conservative_calls_and_no_leak():
    """Beklenmeyen exception durumunda konservatif ai_calls_used=2 atanır ve hata metni sızmaz."""
    fake_ai = FakeAIService()
    prep = _make_preparation(requested_idea_ids=(101,))

    def fake_exec(*, ai_service, work_item):
        raise ZeroDivisionError("Sıfıra bölme hatası: gizli token 12345")

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "failed"
    assert result.ai_calls_used == 2
    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.idea_id == 101
    assert w.reason_code == "content_execution_inconsistent"
    assert w.claims == ()
    assert w.ai_calls_used == 2
    assert "gizli token" not in str(w)
    assert "gizli token" not in str(result)


def test_33_batch_dto_rejects_accepted_result_from_another_attempt():
    """Batch DTO başka attempt'e ait accepted_result nesnesini fail-closed reddeder."""
    item_res = _make_dummy_accepted_item_result(attempt_id=999, idea_id=101, ai_calls_used=1)

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        SocialContentBatchExecutionResult(
            attempt_id=10,
            brief_id=1,
            scoring_run_id=2,
            status="completed",
            requested_idea_ids=(101,),
            already_present_idea_ids=(),
            accepted_results=(item_res,),
            accepted_idea_ids=(101,),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=1,
            replayed=False,
        )

    assert exc_info.value.field == "accepted_results"


def test_34_batch_dto_rejects_mismatched_total_ai_calls_used():
    """Batch DTO toplam ai_calls_used değerinin accepted_results ve warnings toplamı ile uyuşmamasını reddeder."""
    item_res = _make_dummy_accepted_item_result(attempt_id=10, idea_id=101, ai_calls_used=1)

    with pytest.raises(SocialContentBatchExecutionError) as exc_info:
        SocialContentBatchExecutionResult(
            attempt_id=10,
            brief_id=1,
            scoring_run_id=2,
            status="completed",
            requested_idea_ids=(101,),
            already_present_idea_ids=(),
            accepted_results=(item_res,),
            accepted_idea_ids=(101,),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=5,
            replayed=False,
        )

    assert exc_info.value.field == "ai_calls_used"


def test_35_warning_rejects_non_empty_claims_for_error_reasons():
    """content_rejected dışındaki bir reason_code ile claims dolu uyarı oluşturulması reddedilir."""
    for reason in (
        "content_provider_error",
        "content_output_invalid",
        "content_input_invalid",
        "content_repair_provider_error",
        "content_execution_inconsistent",
    ):
        with pytest.raises(SocialContentBatchExecutionError) as exc_info:
            SocialContentBatchWarning(
                idea_id=101,
                reason_code=reason,
                claims=("%80 başarı",),
                ai_calls_used=1,
            )
        assert exc_info.value.field == "claims"


def test_36_content_rejected_warning_carries_claims():
    """content_rejected durumunda SocialContentBatchWarning claims tuple'ını kabul eder."""
    w = SocialContentBatchWarning(
        idea_id=101,
        reason_code="content_rejected",
        claims=("%80 başarı", "en iyi"),
        ai_calls_used=2,
    )
    assert w.reason_code == "content_rejected"
    assert w.claims == ("%80 başarı", "en iyi")
    assert w.ai_calls_used == 2


def test_37_mixed_accepted_rejected_unexpected_calls_accounting():
    """accepted + rejected + unexpected exception karışımında toplam çağrı birebir eşleşir ve üst sınırı aşmaz."""
    resp101 = _make_valid_video_response_dict(caption="101 temiz")
    resp102_dirty1 = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    resp102_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı!")
    fake_ai = FakeAIService([
        json.dumps(resp101),
        json.dumps(resp102_dirty1),
        json.dumps(resp102_dirty2),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102, 103))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 103:
            raise RuntimeError("103 patladı!")
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert result.unresolved_idea_ids == (102, 103)

    # 101: 1 çağrı (accepted)
    # 102: 2 çağrı (rejected)
    # 103: 2 çağrı (unexpected exception)
    # Toplam: 5 çağrı
    expected_calls = sum(r.ai_calls_used for r in result.accepted_results) + sum(w.ai_calls_used for w in result.warnings)
    assert expected_calls == 5
    assert result.ai_calls_used == 5
    assert result.ai_calls_used <= 2 * len(prep.work_items)


def test_38_child_parity_failure_does_not_halt_subsequent_items():
    """İlk work item'ın child parite hatası vermesi sonraki item'ın başarılı yürütülmesini engellemez."""
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp102)])

    prep = _make_preparation(requested_idea_ids=(101, 102))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 101:
            return _make_dummy_accepted_item_result(attempt_id=999, idea_id=101, ai_calls_used=1)
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (102,)
    assert result.unresolved_idea_ids == (101,)
    assert len(result.accepted_results) == 1
    assert result.accepted_results[0].idea_id == 102


def test_39_temperature_none_across_multiple_calls_regression():
    """Tüm AI çağrılarında (ilk üretim ve tamir) temperature parametresi None olmalıdır (temperature=0 yasaktır)."""
    resp1_d = _make_valid_video_response_dict(caption="%80 başarı!")
    resp1_c = _make_valid_video_response_dict(caption="Temiz 1")
    resp2_d = _make_valid_video_response_dict(caption="%80 başarı!")
    resp2_c = _make_valid_video_response_dict(caption="Temiz 2")

    fake_ai = FakeAIService([
        json.dumps(resp1_d), json.dumps(resp1_c),
        json.dumps(resp2_d), json.dumps(resp2_c),
    ])

    prep = _make_preparation(requested_idea_ids=(101, 102))
    result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "completed"
    assert len(fake_ai.call_args) == 4
    for call in fake_ai.call_args:
        assert call.get("temperature") is None
        assert call.get("temperature") != 0


def test_40_callback_called_exactly_once_on_exact_dto_mismatch():
    """Exact DTO olmayan child return durumunda callback tam bir kez çağrılır."""
    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    def fake_exec(*, ai_service, work_item):
        return {"not": "a_dto"}

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(
            ai_service=FakeAIService(),
            preparation=prep,
            on_item_finished=lambda idea_id: callbacks.append(idea_id),
        )

    assert result.status == "failed"
    assert callbacks == [101]


def test_41_callback_called_exactly_once_on_wrong_attempt_id():
    """Yanlış attempt_id child result durumunda callback tam bir kez çağrılır."""
    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    def fake_exec(*, ai_service, work_item):
        return _make_dummy_accepted_item_result(attempt_id=9999, idea_id=101, ai_calls_used=1)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(
            ai_service=FakeAIService(),
            preparation=prep,
            on_item_finished=lambda idea_id: callbacks.append(idea_id),
        )

    assert result.status == "failed"
    assert callbacks == [101]


def test_42_callback_called_exactly_once_on_wrong_idea_id():
    """Yanlış idea_id child result durumunda callback tam bir kez çağrılır."""
    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    def fake_exec(*, ai_service, work_item):
        return _make_dummy_accepted_item_result(attempt_id=prep.attempt_id, idea_id=9999, ai_calls_used=1)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(
            ai_service=FakeAIService(),
            preparation=prep,
            on_item_finished=lambda idea_id: callbacks.append(idea_id),
        )

    assert result.status == "failed"
    assert callbacks == [101]


def test_43_callback_called_exactly_once_on_rejected_item():
    """Rejected item sonrasında callback tam bir kez çağrılır."""
    resp_dirty1 = _make_valid_video_response_dict(caption="%80 başarı formülü!")
    resp_dirty2 = _make_valid_video_response_dict(caption="Hâlâ %80 başarı!")
    fake_ai = FakeAIService([json.dumps(resp_dirty1), json.dumps(resp_dirty2)])

    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    result = execute_social_content_batch(
        ai_service=fake_ai,
        preparation=prep,
        on_item_finished=lambda idea_id: callbacks.append(idea_id),
    )

    assert result.status == "failed"
    assert len(result.warnings) == 1
    assert result.warnings[0].reason_code == "content_rejected"
    assert callbacks == [101]


def test_44_callback_called_exactly_once_on_item_execution_error():
    """Item execution error sonrasında callback tam bir kez çağrılır."""
    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    def fake_exec(*, ai_service, work_item):
        raise SocialContentItemExecutionError(
            "İçerik hatası", reason_code="content_output_invalid", ai_calls_used=1
        )

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(
            ai_service=FakeAIService(),
            preparation=prep,
            on_item_finished=lambda idea_id: callbacks.append(idea_id),
        )

    assert result.status == "failed"
    assert callbacks == [101]


def test_45_callback_called_exactly_once_on_unexpected_exception():
    """Beklenmeyen item exception'ı sonrasında callback tam bir kez çağrılır."""
    prep = _make_preparation(requested_idea_ids=(101,))
    callbacks = []

    def fake_exec(*, ai_service, work_item):
        raise ValueError("Beklenmeyen iç hata")

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(
            ai_service=FakeAIService(),
            preparation=prep,
            on_item_finished=lambda idea_id: callbacks.append(idea_id),
        )

    assert result.status == "failed"
    assert callbacks == [101]


def test_46_callback_exception_bubbles_out_without_becoming_warning():
    """Callback exception'ı batch warning'ine çevrilmeden dışarı fırlar."""
    resp101 = _make_valid_video_response_dict(caption="101 temiz")
    fake_ai = FakeAIService([json.dumps(resp101)])
    prep = _make_preparation(requested_idea_ids=(101,))

    class HeartbeatCrash(RuntimeError):
        pass

    def crashing_callback(idea_id: int):
        raise HeartbeatCrash("Lease patladı")

    with pytest.raises(HeartbeatCrash) as exc_info:
        execute_social_content_batch(
            ai_service=fake_ai,
            preparation=prep,
            on_item_finished=crashing_callback,
        )

    assert "Lease patladı" in str(exc_info.value)


def test_47_callback_exception_prevents_subsequent_work_items():
    """Callback exception'ı fırladığında sonraki work item çalıştırılmaz."""
    resp101 = _make_valid_video_response_dict(caption="101 temiz")
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp101), json.dumps(resp102)])
    prep = _make_preparation(requested_idea_ids=(101, 102))

    called_items = []

    def tracking_callback(idea_id: int):
        called_items.append(idea_id)
        if idea_id == 101:
            raise RuntimeError("101 sonrasında heartbeat koptu")

    with pytest.raises(RuntimeError):
        execute_social_content_batch(
            ai_service=fake_ai,
            preparation=prep,
            on_item_finished=tracking_callback,
        )

    # Yalnızca 101 çalıştırıldı ve callback aldı; 102 kesinlikle çalıştırılmadı
    assert called_items == [101]
    assert len(fake_ai.call_args) == 1


def test_48_callback_not_called_for_already_present_or_replay():
    """Already-present veya completed replay durumunda callback ASLA çağrılmaz."""
    # 1. Replay durumu
    prep_replay = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101, 102),
        work_items=(),
        already_completed=True,
    )
    callbacks_replay = []
    res_replay = execute_social_content_batch(
        ai_service=FakeAIService(),
        preparation=prep_replay,
        on_item_finished=lambda idea_id: callbacks_replay.append(idea_id),
    )
    assert res_replay.replayed is True
    assert callbacks_replay == []

    # 2. Bir item already_present, bir item work_item
    resp102 = _make_valid_video_response_dict(caption="102 temiz")
    fake_ai = FakeAIService([json.dumps(resp102)])
    w_item102 = _make_work_item(idea_id=102)
    prep_mixed = _make_preparation(
        requested_idea_ids=(101, 102),
        already_present_idea_ids=(101,),
        work_items=(w_item102,),
        already_completed=False,
    )
    callbacks_mixed = []
    res_mixed = execute_social_content_batch(
        ai_service=fake_ai,
        preparation=prep_mixed,
        on_item_finished=lambda idea_id: callbacks_mixed.append(idea_id),
    )
    assert res_mixed.status == "completed"
    # Sadece 102 çalıştırıldı, 101 already-present olduğu için callback almadı
    assert callbacks_mixed == [102]


# ==================== ZAMAN BÜTÇESİ + SoftTimeLimitExceeded (defect fix testleri) ====================


def test_49_time_budget_exhausted_before_second_item_partial_with_accepted_persisted():
    """Zaman bütçesi ilk item sonrası aşılırsa: 2. item hiç çalıştırılmaz,
    'time_budget_exhausted' ile unresolved kalır, batch 'partial' döner ve
    1. item'ın kabul edilmiş sonucu accepted_results'ta kalır (persist edilebilir).
    """
    resp101 = _make_valid_video_response_dict(caption="101 temiz video.")
    fake_ai = FakeAIService([json.dumps(resp101)])
    prep = _make_preparation(requested_idea_ids=(101, 102))

    # clock: ilk çağrı (batch_start) = 0.0, ikinci çağrı (item 101 öncesi
    # kontrol) = 0.0 (bütçe aşılmamış), üçüncü çağrı (item 102 öncesi kontrol,
    # 101 işlendikten sonra) = bütçenin üzerinde.
    calls = {"n": 0}

    def fake_clock() -> float:
        calls["n"] += 1
        if calls["n"] <= 2:
            return 0.0
        return 1000.0

    result = execute_social_content_batch(
        ai_service=fake_ai,
        preparation=prep,
        clock=fake_clock,
        time_budget_seconds=900.0,
    )

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert len(result.accepted_results) == 1
    assert result.unresolved_idea_ids == (102,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == 102
    assert result.warnings[0].reason_code == "time_budget_exhausted"
    assert result.warnings[0].ai_calls_used == 0
    # 102 için hiç AI çağrısı yapılmadı (sadece 101 için 1 çağrı)
    assert fake_ai.call_count == 1
    assert result.ai_calls_used == 1


def test_50_time_budget_exhausted_all_items_failed():
    """Bütçe hiç item işlenmeden aşılırsa (ilk kontrolde bile), tüm item'lar
    'time_budget_exhausted' ile unresolved kalır ve batch 'failed' döner.
    """
    fake_ai = FakeAIService()
    prep = _make_preparation(requested_idea_ids=(101, 102))

    # Monoton artan sahte saat: ilk çağrı (batch_start)=0.0, ikinci çağrı
    # (1. item öncesi kontrol)=1000.0 -> bütçe (900) hiç item işlenmeden aşılır.
    counter = {"n": -1}

    def fake_clock() -> float:
        counter["n"] += 1
        return counter["n"] * 1000.0

    result = execute_social_content_batch(
        ai_service=fake_ai,
        preparation=prep,
        clock=fake_clock,
        time_budget_seconds=900.0,
    )

    assert result.status == "failed"
    assert result.accepted_idea_ids == ()
    assert result.unresolved_idea_ids == (101, 102)
    assert all(w.reason_code == "time_budget_exhausted" for w in result.warnings)
    assert all(w.ai_calls_used == 0 for w in result.warnings)
    assert fake_ai.call_count == 0
    assert result.ai_calls_used == 0


def test_51_default_time_budget_constant_is_well_under_soft_time_limit():
    """CONTENT_BATCH_TIME_BUDGET_SECONDS, Celery soft_time_limit=1140s'in
    (app/tasks/generation_tasks.py social_brief_contents_task) belirgin altında
    olmalıdır; aksi halde bütçe kontrolü gerçek zaman aşımını önleyemez.
    """
    from app.core.social.content_batch_execution import CONTENT_BATCH_TIME_BUDGET_SECONDS

    assert CONTENT_BATCH_TIME_BUDGET_SECONDS > 0
    assert CONTENT_BATCH_TIME_BUDGET_SECONDS < 1140
    assert (1140 - CONTENT_BATCH_TIME_BUDGET_SECONDS) >= 120  # en az 2 dk marj


def test_52_soft_time_limit_exceeded_during_item_absorbed_as_partial_not_propagated():
    """SoftTimeLimitExceeded bir item'ın işlenmesi sırasında (proaktif bütçe
    kontrolünü atlayarak) fırlarsa, batch bunu yutmaz AMA dışarı da FIRLATMAZ:
    o item ve işlenmemiş kalan item'lar 'time_budget_exhausted' ile unresolved
    bırakılır, batch normal 'partial' yolundan zarif biçimde döner.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    resp101 = _make_valid_video_response_dict(caption="101 temiz")
    fake_ai = FakeAIService([json.dumps(resp101)])
    prep = _make_preparation(requested_idea_ids=(101, 102, 103))

    real_exec = execute_social_content_work_item

    def fake_exec(*, ai_service, work_item):
        if work_item.idea_id == 102:
            raise SoftTimeLimitExceeded("soft time limit mid-item")
        return real_exec(ai_service=ai_service, work_item=work_item)

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=fake_exec,
    ):
        result = execute_social_content_batch(ai_service=fake_ai, preparation=prep)

    assert result.status == "partial"
    assert result.accepted_idea_ids == (101,)
    assert result.unresolved_idea_ids == (102, 103)
    reasons = {w.idea_id: w.reason_code for w in result.warnings}
    assert reasons[102] == "time_budget_exhausted"
    assert reasons[103] == "time_budget_exhausted"
    # 103 hiç çalıştırılmadı (fake_ai sadece 1 kez çağrıldı, 101 için)
    assert fake_ai.call_count == 1


def test_53_soft_time_limit_exceeded_not_swallowed_in_process_single_work_item():
    """_process_single_work_item SoftTimeLimitExceeded'i sıradan bir item hatası
    gibi warning'e çevirip yutmaz; çağırana (batch executor) fırlatır.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    from app.core.social.content_batch_execution import _process_single_work_item

    prep = _make_preparation(requested_idea_ids=(101,))
    item = prep.work_items[0]

    def raising_exec(*, ai_service, work_item):
        raise SoftTimeLimitExceeded("soft time limit")

    accepted_results_list: list = []
    accepted_ids_set: set[int] = set()
    warnings_map: dict = {}

    with patch(
        "app.core.social.content_batch_execution.execute_social_content_work_item",
        side_effect=raising_exec,
    ):
        with pytest.raises(SoftTimeLimitExceeded):
            _process_single_work_item(
                ai_service=FakeAIService(),
                item=item,
                preparation=prep,
                accepted_results_list=accepted_results_list,
                accepted_ids_set=accepted_ids_set,
                warnings_map=warnings_map,
            )

    assert accepted_results_list == []
    assert warnings_map == {}


