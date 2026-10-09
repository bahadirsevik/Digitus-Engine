# -*- coding: utf-8 -*-
"""Unit tests for Tek Birleşik Content Repair Promptu ve Tek Çağrılı Repair Adaptörü (F1-G.4).

Bu test süiti:
A. Girdi ve Önkoşul Doğrulaması:
   - Exact type kontrolleri (prompt_input, original_content, grounding_context)
   - Clean içerik gönderildiğinde 0 AI çağrısı ve CONTENT_REPAIR_NOT_REQUIRED
   - Forged veya manipüle edilmiş karar nesnelerinin geçersiz kılınması (içeride yeniden hesaplama)
   - action=accept veya reject durumunda repair prompt builder'ın fail-closed reddi
   - Format ve target uyumsuzluğunun AI öncesi tespiti
B. Repair Prompt Sözleşmesi ve Sınır Güvenliği:
   - Tam olarak tek bir açılış (<INPUT_JSON>) ve kapanış (</INPUT_JSON>) etiketi
   - Caption/hook/facts/claims içinde </INPUT_JSON> olsa bile sınır kırılamaz (Unicode escape)
   - Deterministik serileştirme ve claim-free statik şablon
   - Tekil ve birleşik reason_codes (claim, duration, claim+duration) talimat blokları
   - Orijinal içeriğin eksiksiz yapılandırılmış aktarımı
   - Tüm platform ve formatlarda prompt üretim garantisi
C. AI Çağrı Protokolü:
   - Tam olarak tek bir AI çağrısı (ai_calls_used == 1, fake_ai.call_count == 1)
   - temperature=None garantisi
   - response_schema ve max_tokens=12000 sözleşmesi
   - Provider hatasında fail-closed CONTENT_REPAIR_PROVIDER_ERROR (0 retry)
   - Geçersiz çıktıda fail-closed CONTENT_REPAIR_OUTPUT_INVALID (0 retry, telemetri bildirimi)
   - Fallback/sahte içerik üretilmeme garantisi
D. Post-Repair Karar Matrisi:
   - Düzeltilmiş temiz ve süre uyumlu çıktı -> action="accept", reason_codes=()
   - Claim temizlenmiş fakat süre hâlâ mismatch -> action="accept", warnings'e "duration_mismatch" eklenir
   - Claim düzeltilememiş -> action="reject", reason_codes en az ("ungrounded_claim",)
   - Hem claim hem süre bozuk kalmış -> action="reject", reason_codes=("ungrounded_claim", "duration_mismatch")
   - Voiceover soft warning korunur, tek başına reject veya ikinci repair üretmez
   - Reject sonucunda claims/warnings bilgisi SocialContentRepairAIResult içinde korunur
E. Format Paritesi:
   - Post, Story, Carousel, Thread, Video, Reels ve Short formatlarında başarılı düzeltme
senaryolarını fake AI servisi ile uçtan uca doğrular.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from app.core.social.content_contract import (
    CONTENT_CONTRACT_INVALID_DURATION,
    CONTENT_CONTRACT_INVALID_TIMELINE,
    ContentTargetSpec,
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedThreadPayload,
    ValidatedThreadPost,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
)
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    SocialContentQualityDecision,
    SocialContentQualityError,
    evaluate_social_content_quality,
)
from app.generators.social.brief_content_generator import (
    MAX_CONTENT_TOKENS,
    SocialBriefContentGenerator,
    SocialContentRepairAIResult,
    SocialContentRepairError,
)
from app.generators.social.brief_content_prompt import (
    SocialContentKeywordInput,
    SocialContentPromptError,
    SocialContentPromptInput,
    build_social_content_repair_prompt,
    validate_grounding_prompt_parity,
)


# ==================== TEST YARDIMCILARI & FAKE AI ====================

class FakeCollector:
    """Telemetri mark_current_attempt_failed çağrılarını kaydeden fake collector."""

    def __init__(self) -> None:
        self.failed_reasons: list[str] = []

    def mark_current_attempt_failed(self, reason: str) -> bool:
        self.failed_reasons.append(reason)
        return True

    def logical_request(self):
        from contextlib import nullcontext
        return nullcontext()


class FakeAIService:
    """F1-G.4 testleri için izole sahte AI servisi."""

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


def _make_hook(text: str = "Doğal içerikli cilt bakımı", style: str = "curiosity", ab_score: float | None = None) -> ValidatedHook:
    return ValidatedHook(text=text, style=style, ab_score=ab_score)


def _make_prompt_input(
    *,
    attempt_id: int = 10,
    idea_id: int = 20,
    target_spec: ContentTargetSpec | None = None,
    idea_title: str = "Test Başlık",
    idea_description: str = "Test açıklama stratejisi.",
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
        primary_keyword = SocialContentKeywordInput(keyword_id=1, keyword="organik cilt bakımı")
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


def _make_grounding_context(
    inp: SocialContentPromptInput | None = None,
    *,
    primary_keyword: str | None = None,
    product_facts: str | None = None,
    trusted_brand_usp: str | None = None,
) -> SocialContentGroundingContext:
    if inp is not None:
        return SocialContentGroundingContext(
            primary_keyword=primary_keyword if primary_keyword is not None else inp.primary_keyword.keyword,
            product_facts=product_facts if product_facts is not None else inp.product_facts,
            trusted_brand_usp=trusted_brand_usp if trusted_brand_usp is not None else inp.trusted_brand_usp,
        )
    return SocialContentGroundingContext(
        primary_keyword=primary_keyword if primary_keyword is not None else "organik cilt bakımı",
        product_facts=product_facts if product_facts is not None else "Dermatolojik olarak test edilmiştir.",
        trusted_brand_usp=trusted_brand_usp if trusted_brand_usp is not None else "Organik soğuk sıkım yağlar",
    )


def _make_video_content(
    *,
    caption: str = "Doğal cilt bakım rutini önerileri.",
    hooks: tuple[ValidatedHook, ...] | None = None,
    duration_status: str = "valid",
    actual_duration_sec: int | None = 25,
    validation_warnings: tuple[str, ...] = (),
) -> ValidatedSocialContent:
    if hooks is None:
        hooks = (_make_hook(),)
    segments = (
        ValidatedVideoSegment(
            start_sec=0,
            end_sec=actual_duration_sec or 25,
            scene="Model ürünü gösterir",
            on_screen_text="Adım adım uygulama",
            voiceover="Cildinizi nazikçe temizleyin.",
        ),
    )
    return ValidatedSocialContent(
        hooks=hooks,
        caption=caption,
        format_payload=ValidatedVideoPayload(kind="video", segments=segments),
        visual_suggestion="Aydınlık doğal ışık",
        video_concept="B-roll geçişleri",
        cta_text="Detaylar profildeki linkte.",
        hashtags=("ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik"),
        industry_posting_suggestion="Akşam saatleri",
        platform_notes=None,
        duration_status=duration_status,
        actual_duration_sec=actual_duration_sec,
        validation_warnings=validation_warnings,
        scenario=None,
    )


def _make_post_content(
    *,
    caption: str = "Doğal cilt bakım rutini önerileri.",
    hooks: tuple[ValidatedHook, ...] | None = None,
    duration_status: str = "not_applicable",
    actual_duration_sec: int | None = None,
    validation_warnings: tuple[str, ...] = (),
) -> ValidatedSocialContent:
    if hooks is None:
        hooks = (_make_hook(),)
    return ValidatedSocialContent(
        hooks=hooks,
        caption=caption,
        format_payload=None,
        visual_suggestion="Ürün fotoğrafı",
        video_concept=None,
        cta_text="Detaylar profildeki linkte.",
        hashtags=("ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik"),
        industry_posting_suggestion="Sabah saatleri",
        platform_notes=None,
        duration_status=duration_status,
        actual_duration_sec=actual_duration_sec,
        validation_warnings=validation_warnings,
        scenario=None,
    )


def _make_valid_video_response_dict(*, end_sec: int = 24, caption: str = "Düzeltilmiş temiz metin") -> dict[str, Any]:
    return {
        "hooks": [
            {"text": "Cildiniz için temiz içerikli 3 kural!", "style": "curiosity"},
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


def _make_valid_post_response_dict(*, caption: str = "Düzeltilmiş temiz gönderi metni") -> dict[str, Any]:
    return {
        "hooks": [
            {"text": "Günlük bakımınız için doğal dokunuş!", "style": "relatable"},
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


# ==================== TEST GRUBU 1: GİRDİ VE ÖNKOŞULLAR ====================

def test_p01_exact_type_checks_reject_invalid_inputs():
    """repair_once çağrısında exact type kontrolü yapılır; yabancı tipler 0 AI çağrısıyla reddedilir."""
    fake_ai = FakeAIService()
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    content = _make_video_content()
    context = _make_grounding_context(inp)

    # 1. bad prompt_input
    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once("bad_input", content, context)  # type: ignore
    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"

    # 2. bad original_content
    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, "bad_content", context)  # type: ignore
    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"

    # 3. bad grounding_context
    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, content, "bad_context")  # type: ignore
    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"

    assert fake_ai.call_count == 0


def test_p02_clean_content_rejected_with_not_required():
    """Zaten temiz ve süre uyumlu olan içerik repair'e gönderilirse 0 AI çağrısıyla CONTENT_REPAIR_NOT_REQUIRED döner."""
    fake_ai = FakeAIService()
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    # Temiz ve valid içerik
    content = _make_video_content(caption="Doğal cilt bakım önerileri.", duration_status="valid")
    context = _make_grounding_context(inp)

    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, content, context)

    assert exc_info.value.error_code == "CONTENT_REPAIR_NOT_REQUIRED"
    assert fake_ai.call_count == 0


def test_p03_forged_quality_decision_cannot_bypass_engine():
    """build_social_content_repair_prompt fonksiyonuna çelişkili veya forged karar verilirse fail-closed reddedilir."""
    inp = _make_prompt_input()
    # original_content duration mismatch, fakat dışarıdan ungrounded_claim kararı iddia ediliyor
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)
    forged_decision = SocialContentQualityDecision(
        action="repair",
        reason_codes=("ungrounded_claim",),
        claims=("%80",),
        warnings=(),
        grounding_clean=False,
        duration_acceptable=True,
        repair_attempted=False,
    )

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, content, context, forged_decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_info.value.field == "quality_decision"


def test_p04_accept_or_reject_decision_cannot_trigger_prompt_builder():
    """quality_decision.action değeri 'accept' veya 'reject' ise prompt oluşturulamaz."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="valid")
    context = _make_grounding_context(inp)
    accept_decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, content, context, accept_decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_NOT_REQUIRED"


def test_p05_format_mismatch_with_spec_rejected_pre_ai():
    """original_content'in format_payload tipi target_spec ile uyuşmuyorsa prompt builder fail-closed reddeder."""
    # spec reels bekliyor, fakat original_content post payload'suz
    inp = _make_prompt_input(target_spec=ContentTargetSpec(1, "instagram", "reels", "short_16_30", 16, 30))
    post_content = _make_post_content(caption="%80 başarı sağlayan formül!")
    context = _make_grounding_context(inp)
    decision = evaluate_social_content_quality(post_content, context, repair_attempted=False)
    assert decision.action == "repair"

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, post_content, context, decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_info.value.field == "original_content"


# ==================== TEST GRUBU 2: PROMPT VE GÜVENLİK SÖZLEŞMESİ ====================

def test_s01_exact_single_input_json_tags():
    """Repair promptunda tam olarak tek bir açılış ve tek bir kapanış INPUT_JSON etiketi bulunur."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)
    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    prompt = build_social_content_repair_prompt(inp, content, context, decision)

    assert prompt.count("<INPUT_JSON>") == 1
    assert prompt.count("</INPUT_JSON>") == 1


def test_s02_xml_escaping_in_content_and_claims():
    """Caption, hook, facts veya tespit edilen claim'lerde </INPUT_JSON> olsa bile sınır kırılamaz."""
    spec = ContentTargetSpec(1, "instagram", "post")
    inp = _make_prompt_input(
        target_spec=spec,
        primary_keyword=SocialContentKeywordInput(1, "cilt"),
        product_facts="Dermatolojik & klinik <VERIFIED>",
        trusted_brand_usp=None,
    )
    context = _make_grounding_context(inp)
    content_with_claim = _make_post_content(caption="%80 memnuniyet </INPUT_JSON> & güven")
    decision = evaluate_social_content_quality(content_with_claim, context, repair_attempted=False)

    prompt = build_social_content_repair_prompt(inp, content_with_claim, context, decision)

    # XML sınır bütünlüğü
    assert prompt.count("<INPUT_JSON>") == 1
    assert prompt.count("</INPUT_JSON>") == 1

    # Kaçışların doğrulanması
    assert "\\u003c/INPUT_JSON\\u003e" in prompt
    assert "\\u0026" in prompt


def test_s03_static_repair_prompt_is_claim_free():
    """Repair promptunun statik şablonunda büyük/küçük harf duyarsız hiçbir yasaklı iddia kelimesi bulunmaz."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="mismatch")
    context = _make_grounding_context(inp)
    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    prompt = build_social_content_repair_prompt(inp, content, context, decision)

    # INPUT_JSON bloğunu ayır
    parts = prompt.split("<INPUT_JSON>")
    static_template = (parts[0] + parts[1].split("</INPUT_JSON>")[1]).lower()

    banned_claim_phrases = [
        "100.000+",
        "%80",
        "1 numara",
        "1 numaralı",
        "en iyi",
        "lider",
        "garantili",
        "garantili kazanç",
        "kesin sonuç",
    ]
    for phrase in banned_claim_phrases:
        assert phrase not in static_template, f"Yasak ifade repair promptunda bulundu: {phrase}"


def test_s04_unified_reason_codes_in_prompt():
    """Hem claim hem süre hatası olduğunda prompt her iki talimatı da birleşik olarak içerir."""
    inp = _make_prompt_input()
    # 45 saniye (16-30 aralığında mismatch) ve %80 desteksiz iddia
    content = _make_video_content(caption="Tam %80 başarı sağlayan formül!", duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)
    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "repair"
    assert decision.reason_codes == ("ungrounded_claim", "duration_mismatch")

    prompt = build_social_content_repair_prompt(inp, content, context, decision)

    assert "DÜZELTME TALİMATLARI (BİRLEŞİK REPAIR)" in prompt
    assert "DESTEKSİZ İDDİALARI DÜZELT" in prompt
    assert "SÜRE VE ZAMAN ÇİZELGESİNİ UYUMLU HALE GETİR" in prompt
    assert '"reason_codes": [\n      "ungrounded_claim",\n      "duration_mismatch"\n    ]' in prompt


# ==================== TEST GRUBU 3: TEK ÇAĞRILI AI ADAPTÖRÜ ====================

def test_a01_successful_repair_exactly_one_ai_call():
    """Başarılı tamirde tam olarak 1 AI çağrısı yapılır, temperature None'dır ve accept sonucu döner."""
    resp_dict = _make_valid_video_response_dict(end_sec=22)
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    # Orijinal içerik süresi 45 sn (mismatch)
    original_content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original_content, context)

    assert isinstance(result, SocialContentRepairAIResult)
    assert result.ai_calls_used == 1
    assert fake_ai.call_count == 1
    assert result.attempt_id == 10
    assert result.idea_id == 20

    # AI çağrı parametreleri
    call_args = fake_ai.call_args[0]
    assert call_args["temperature"] is None
    assert call_args["max_tokens"] == MAX_CONTENT_TOKENS
    assert call_args["response_schema"] is not None

    # Karar accept olmalıdır
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.grounding_clean is True
    assert result.quality_decision.duration_acceptable is True
    assert result.content.actual_duration_sec == 22


def test_a02_provider_exception_fail_closed_no_retry():
    """Sağlayıcı istisnasında ek çağrı yapılmaz; tek çağrıdan sonra CONTENT_REPAIR_PROVIDER_ERROR fırlatılır."""
    fake_ai = FakeAIService(raise_exc=RuntimeError("Google Gemini API down"))
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(duration_status="mismatch")
    context = _make_grounding_context(inp)

    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, original_content, context)

    assert exc_info.value.error_code == "CONTENT_REPAIR_PROVIDER_ERROR"
    assert fake_ai.call_count == 1
    assert "Google Gemini API down" not in str(exc_info.value)


def test_a03_invalid_output_fail_closed_telemetry_and_no_retry():
    """Tamir çıktısı şemaya uymadığında telemetri işaretlenir, CONTENT_REPAIR_OUTPUT_INVALID fırlatılır ve 2. çağrı yapılmaz."""
    # Video başlangıcı 0 yerine 10'da başlıyor (timeline hatası)
    invalid_resp = _make_valid_video_response_dict()
    invalid_resp["format_payload"]["segments"][0]["start_sec"] = 10

    fake_ai = FakeAIService(responses=[json.dumps(invalid_resp)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(duration_status="mismatch")
    context = _make_grounding_context(inp)

    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, original_content, context)

    assert exc_info.value.error_code == "CONTENT_REPAIR_OUTPUT_INVALID"
    assert exc_info.value.validation_error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert fake_ai.call_count == 1
    assert len(fake_ai.collector.failed_reasons) == 1
    assert "start_sec" in fake_ai.collector.failed_reasons[0]


# ==================== TEST GRUBU 4: POST-REPAIR KARAR MATRİSİ ====================

def test_d01_repair_still_mismatched_duration_accepted_with_warning():
    """Repair sonrasında grounding temiz ancak video süresi hâlâ hedef aralığın dışındaysa accept edilir ve duration_mismatch warning eklenir."""
    # Hedef 16-30 saniye, tamir edilen içerik 40 saniye (hâlâ mismatch)
    resp_dict = _make_valid_video_response_dict(end_sec=40)
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original_content, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.reason_codes == ("duration_mismatch",)
    assert result.quality_decision.duration_acceptable is False
    assert result.quality_decision.grounding_clean is True
    assert "duration_mismatch" in result.quality_decision.warnings


def test_d02_repair_claim_persists_rejected():
    """Repair sonrasında desteksiz iddia devam ediyorsa KESİNLİKLE reject döner ve ikinci repair yapılmaz."""
    # Tamir çıktısı hâlâ ungrounded %80 içeriyor
    resp_dict = _make_valid_video_response_dict(end_sec=22, caption="Yine de yüzde 80 memnuniyet!")
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(caption="İlk iddia: 100.000+ kullanıcı", duration_status="valid")
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original_content, context)

    assert result.ai_calls_used == 1
    assert fake_ai.call_count == 1
    assert result.quality_decision.action == "reject"
    assert "ungrounded_claim" in result.quality_decision.reason_codes
    assert result.quality_decision.grounding_clean is False
    assert len(result.quality_decision.claims) > 0


def test_d03_repair_claim_and_duration_both_persist_rejected_with_two_reason_codes():
    """Repair sonrasında hem claim hem duration bozuk kalmışsa reject edilir ve iki reason code korunur."""
    # 40 sn (mismatch) ve yüzde 80 (ungrounded claim)
    resp_dict = _make_valid_video_response_dict(end_sec=40, caption="Hâlâ yüzde 80 memnuniyet!")
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(caption="İlk iddia", duration_status="mismatch")
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original_content, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "reject"
    assert result.quality_decision.reason_codes == ("ungrounded_claim", "duration_mismatch")
    assert "duration_mismatch" in result.quality_decision.warnings


def test_d04_voiceover_warning_alone_does_not_reject():
    """Seslendirme hızı uyarısı tamir edilmiş içerikte bulunsa bile tek başına reject tetiklemez; accept döner."""
    resp_dict = _make_valid_video_response_dict(end_sec=20)
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input()
    original_content = _make_video_content(duration_status="mismatch")
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original_content, context)

    assert result.quality_decision.action == "accept"
    # İkinci repair yapılmadığı doğrulanır
    assert result.ai_calls_used == 1


# ==================== TEST GRUBU 5: FORMAT PARİTESİ ====================

@pytest.mark.parametrize(
    "platform,content_format",
    [
        ("instagram", "post"),
        ("instagram", "story"),
        ("twitter", "post"),
        ("linkedin", "post"),
    ],
)
def test_f01_post_and_story_formats_repair_claim(platform: str, content_format: str):
    """Post ve Statik Story formatlarında desteksiz iddia tek çağrıda başarıyla tamir edilir."""
    spec = ContentTargetSpec(target_id=1, platform=platform, content_format=content_format)
    inp = _make_prompt_input(target_spec=spec)
    # Orijinal postta ungrounded claim var
    original = _make_post_content(caption="1 numara organik bakım ürünü!")
    context = _make_grounding_context(inp)

    clean_resp = _make_valid_post_response_dict(caption="Cildinizi tazeleyen doğal bakım adımları.")
    fake_ai = FakeAIService(responses=[json.dumps(clean_resp)])
    generator = SocialBriefContentGenerator(fake_ai)

    result = generator.repair_once(inp, original, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.grounding_clean is True
    assert result.content.format_payload is None


def test_f02_carousel_format_repair_claim():
    """Carousel formatında desteksiz iddia tamir edilir."""
    spec = ContentTargetSpec(target_id=2, platform="instagram", content_format="carousel")
    inp = _make_prompt_input(target_spec=spec)

    slide = ValidatedCarouselSlide(position=1, headline="Başlık", body="Gövde", visual_direction="Yön")
    original = ValidatedSocialContent(
        hooks=(_make_hook(),),
        caption="Yüzde 80 memnuniyet ile kaydırın!",
        format_payload=ValidatedCarouselPayload(kind="carousel", slides=(slide, ValidatedCarouselSlide(2, "B2", "G2", "Y2"))),
        visual_suggestion="Kart",
        video_concept=None,
        cta_text="Kaydedin",
        hashtags=("cilt", "bakim", "organik", "dogal", "nemlendirici"),
        industry_posting_suggestion=None,
        platform_notes=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=(),
        scenario=None,
    )
    context = _make_grounding_context(inp)

    resp = {
        "hooks": [{"text": "Cilt bakımında 2 önemli kural!", "style": "curiosity"}],
        "caption": "Doğru temizleme ve nemlendirme rehberi.",
        "cta_text": "Kaydedin ve uygulayın.",
        "hashtags": ["cilt", "bakim", "organik", "dogal", "nemlendirici"],
        "format_payload": {
            "kind": "carousel",
            "slides": [
                {"position": 1, "headline": "Kural 1", "body": "Sabah temizliği", "visual_direction": "Kapak"},
                {"position": 2, "headline": "Kural 2", "body": "Akşam nemlendirmesi", "visual_direction": "İçerik"},
            ],
        },
        "visual_suggestion": "Temiz infografik",
        "video_concept": None,
        "industry_posting_suggestion": None,
        "platform_notes": None,
    }
    fake_ai = FakeAIService(responses=[json.dumps(resp)])
    generator = SocialBriefContentGenerator(fake_ai)

    result = generator.repair_once(inp, original, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "accept"
    assert isinstance(result.content.format_payload, ValidatedCarouselPayload)


def test_f03_thread_format_repair_claim():
    """Thread formatında desteksiz iddia tamir edilir."""
    spec = ContentTargetSpec(target_id=7, platform="twitter", content_format="thread")
    inp = _make_prompt_input(target_spec=spec)

    post1 = ValidatedThreadPost(position=1, text="1/2 Tweet bir")
    post2 = ValidatedThreadPost(position=2, text="2/2 Tweet iki")
    original = ValidatedSocialContent(
        hooks=(_make_hook(),),
        caption="1 numara organik formül hakkında bir flood: 🧵",
        format_payload=ValidatedThreadPayload(kind="thread", posts=(post1, post2)),
        visual_suggestion=None,
        video_concept=None,
        cta_text="RT yapın",
        hashtags=("cilt", "bakim", "organik", "dogal", "nemlendirici"),
        industry_posting_suggestion=None,
        platform_notes=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=(),
        scenario=None,
    )
    context = _make_grounding_context(inp)

    resp = {
        "hooks": [{"text": "Cilt sağlığını korumanın adımları 🧵", "style": "curiosity"}],
        "caption": "Doğal cilt sağlığı rehberi.",
        "cta_text": "Faydalı bulduysanız paylaşın.",
        "hashtags": ["cilt", "bakim", "organik", "dogal", "nemlendirici"],
        "format_payload": {
            "kind": "thread",
            "posts": [
                {"position": 1, "text": "1/2 Cilt bariyerinizi korumak için nazik temizleyiciler kullanın."},
                {"position": 2, "text": "2/2 Nem dengesini koruyarak canlılık sağlayın."},
            ],
        },
        "visual_suggestion": None,
        "video_concept": None,
        "industry_posting_suggestion": None,
        "platform_notes": None,
    }
    fake_ai = FakeAIService(responses=[json.dumps(resp)])
    generator = SocialBriefContentGenerator(fake_ai)

    result = generator.repair_once(inp, original, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "accept"
    assert isinstance(result.content.format_payload, ValidatedThreadPayload)


@pytest.mark.parametrize(
    "platform,content_format,preset_id,min_sec,max_sec",
    [
        ("instagram", "reels", "short_16_30", 16, 30),
        ("tiktok", "short", "short_31_60", 31, 60),
        ("twitter", "video", "x_16_30", 16, 30),
        ("linkedin", "video", "long_60_180", 60, 180),
        ("youtube", "short", "short_1_15", 1, 15),
        ("youtube", "video", "long_181_300", 181, 300),
    ],
)
def test_f04_all_video_types_repair_duration_and_claim(
    platform: str,
    content_format: str,
    preset_id: str,
    min_sec: int,
    max_sec: int,
):
    """Tüm kanonik video formatlarında hem süre hem iddia tek çağrıda tamir edilir."""
    spec = ContentTargetSpec(
        target_id=5,
        platform=platform,
        content_format=content_format,
        duration_preset_id=preset_id,
        duration_min_sec=min_sec,
        duration_max_sec=max_sec,
    )
    inp = _make_prompt_input(target_spec=spec)

    # Hedef dışında (örneğin max_sec + 50) ve ungrounded iddialı orijinal içerik
    bad_duration = max_sec + 50
    original = _make_video_content(
        caption="En iyi organik bakım ile %80 memnuniyet!",
        duration_status="mismatch",
        actual_duration_sec=bad_duration,
    )
    context = _make_grounding_context(inp)

    # Hedef aralıkta tamir edilmiş çıktı
    valid_target_sec = (min_sec + max_sec) // 2
    clean_resp = _make_valid_video_response_dict(end_sec=valid_target_sec, caption="Doğal cilt temizliği adımları.")
    fake_ai = FakeAIService(responses=[json.dumps(clean_resp)])
    generator = SocialBriefContentGenerator(fake_ai)

    result = generator.repair_once(inp, original, context)

    assert result.ai_calls_used == 1
    assert result.quality_decision.action == "accept"
    assert result.quality_decision.duration_acceptable is True
    assert result.quality_decision.grounding_clean is True
    assert result.content.actual_duration_sec == valid_target_sec


# ==================== TEST GRUBU 6: IMMUTABILITY ====================

def test_m01_repair_result_immutability():
    """SocialContentRepairAIResult nesnesi dondurulmuştur (frozen dataclass)."""
    resp_dict = _make_valid_post_response_dict()
    fake_ai = FakeAIService(responses=[json.dumps(resp_dict)])
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input(target_spec=ContentTargetSpec(1, "instagram", "post"))
    original = _make_post_content(caption="1 numara organik marka!")
    context = _make_grounding_context(inp)

    result = generator.repair_once(inp, original, context)

    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.ai_calls_used = 99  # type: ignore


# ==================== TEST GRUBU 7: GROUNDING PARİTESİ VE OTORİTE (F1-G.4a) ====================

def test_parity_keyword_mismatch_rejected_0_ai():
    """primary_keyword ile grounding_context.primary_keyword uyuşmazlığı 0 AI çağrısıyla CONTENT_REPAIR_INVALID_INPUT döndürür ve metin sızdırmaz."""
    fake_ai = FakeAIService()
    generator = SocialBriefContentGenerator(fake_ai)

    inp = _make_prompt_input(primary_keyword=SocialContentKeywordInput(keyword_id=1, keyword="organik cilt bakımı"))
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    # Farklı keyword
    mismatched_context = SocialContentGroundingContext(
        primary_keyword="farklı arama kelimesi",
        product_facts=inp.product_facts,
        trusted_brand_usp=inp.trusted_brand_usp,
    )

    # 1. repair_once seviyesinde kontrol (0 AI çağrısı)
    with pytest.raises(SocialContentRepairError) as exc_info:
        generator.repair_once(inp, content, mismatched_context)

    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    err_str = str(exc_info.value).lower()
    assert "organik cilt bakımı" not in err_str
    assert "farklı arama kelimesi" not in err_str

    # 2. validate_grounding_prompt_parity doğrudan kontrolü
    with pytest.raises(SocialContentPromptError) as p_exc:
        validate_grounding_prompt_parity(mismatched_context, inp)
    assert p_exc.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert p_exc.value.field == "primary_keyword"
    assert "organik" not in str(p_exc.value).lower()

    # 3. build_social_content_repair_prompt doğrudan kontrolü
    with pytest.raises(SocialContentPromptError) as b_exc:
        build_social_content_repair_prompt(inp, content, mismatched_context)
    assert b_exc.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert b_exc.value.field == "primary_keyword"


def test_parity_product_facts_mismatch_rejected_0_ai():
    """product_facts uyuşmazlığı (None vs str, str vs None, iki farklı str) 0 AI çağrısıyla reddedilir ve veri sızdırmaz."""
    fake_ai = FakeAIService()
    generator = SocialBriefContentGenerator(fake_ai)
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)

    # Vaka 1: inp.product_facts is None, context.product_facts is str
    inp_none = _make_prompt_input(product_facts=None)
    ctx_str = SocialContentGroundingContext(
        primary_keyword=inp_none.primary_keyword.keyword,
        product_facts="Klinik ve laboratuvar testleri yapılmıştır.",
        trusted_brand_usp=inp_none.trusted_brand_usp,
    )
    with pytest.raises(SocialContentRepairError) as exc_1:
        generator.repair_once(inp_none, content, ctx_str)
    assert exc_1.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "klinik" not in str(exc_1.value).lower()

    # Vaka 2: inp.product_facts is str, context.product_facts is None
    inp_str = _make_prompt_input(product_facts="Gizli klinik bilgi.")
    ctx_none = SocialContentGroundingContext(
        primary_keyword=inp_str.primary_keyword.keyword,
        product_facts=None,
        trusted_brand_usp=inp_str.trusted_brand_usp,
    )
    with pytest.raises(SocialContentRepairError) as exc_2:
        generator.repair_once(inp_str, content, ctx_none)
    assert exc_2.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "gizli" not in str(exc_2.value).lower()

    # Vaka 3: İki farklı string
    inp_diff = _make_prompt_input(product_facts="Fact Alpha 123")
    ctx_diff = SocialContentGroundingContext(
        primary_keyword=inp_diff.primary_keyword.keyword,
        product_facts="Fact Beta 456",
        trusted_brand_usp=inp_diff.trusted_brand_usp,
    )
    with pytest.raises(SocialContentRepairError) as exc_3:
        generator.repair_once(inp_diff, content, ctx_diff)
    assert exc_3.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "alpha" not in str(exc_3.value).lower()
    assert "beta" not in str(exc_3.value).lower()

    # Prompt builder doğrudan kontrolü
    with pytest.raises(SocialContentPromptError) as b_exc:
        build_social_content_repair_prompt(inp_diff, content, ctx_diff)
    assert b_exc.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert b_exc.value.field == "product_facts"


def test_parity_trusted_brand_usp_mismatch_rejected_0_ai():
    """trusted_brand_usp uyuşmazlığı (None vs str, str vs None, iki farklı str) 0 AI çağrısıyla reddedilir ve veri sızdırmaz."""
    fake_ai = FakeAIService()
    generator = SocialBriefContentGenerator(fake_ai)
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)

    # Vaka 1: inp.trusted_brand_usp is None, context.trusted_brand_usp is str
    inp_none = _make_prompt_input(trusted_brand_usp=None)
    ctx_str = SocialContentGroundingContext(
        primary_keyword=inp_none.primary_keyword.keyword,
        product_facts=inp_none.product_facts,
        trusted_brand_usp="Organik soğuk sıkım garantisi.",
    )
    with pytest.raises(SocialContentRepairError) as exc_1:
        generator.repair_once(inp_none, content, ctx_str)
    assert exc_1.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "soğuk" not in str(exc_1.value).lower()

    # Vaka 2: inp.trusted_brand_usp is str, context.trusted_brand_usp is None
    inp_str = _make_prompt_input(trusted_brand_usp="Özel formül patentli USP.")
    ctx_none = SocialContentGroundingContext(
        primary_keyword=inp_str.primary_keyword.keyword,
        product_facts=inp_str.product_facts,
        trusted_brand_usp=None,
    )
    with pytest.raises(SocialContentRepairError) as exc_2:
        generator.repair_once(inp_str, content, ctx_none)
    assert exc_2.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "patentli" not in str(exc_2.value).lower()

    # Vaka 3: İki farklı string
    inp_diff = _make_prompt_input(trusted_brand_usp="GizliFormulA")
    ctx_diff = SocialContentGroundingContext(
        primary_keyword=inp_diff.primary_keyword.keyword,
        product_facts=inp_diff.product_facts,
        trusted_brand_usp="PatentliTeknolojiB",
    )
    with pytest.raises(SocialContentRepairError) as exc_3:
        generator.repair_once(inp_diff, content, ctx_diff)
    assert exc_3.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert fake_ai.call_count == 0
    assert "gizliformula" not in str(exc_3.value).lower()
    assert "patentliteknolojib" not in str(exc_3.value).lower()

    # Prompt builder doğrudan kontrolü
    with pytest.raises(SocialContentPromptError) as b_exc:
        build_social_content_repair_prompt(inp_diff, content, ctx_diff)
    assert b_exc.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert b_exc.value.field == "trusted_brand_usp"


def test_parity_strict_equality_no_coercion_or_casefold():
    """Parite kontrolü tam (exact) eşitlik arar; casefold veya normalization yapmaz."""
    # Harf büyüklüğü farkı
    inp = _make_prompt_input(
        primary_keyword=SocialContentKeywordInput(1, "organik cilt bakımı"),
        product_facts="Dermatolojik Olarak Test Edilmiştir.",
        trusted_brand_usp="Organik Soğuk Sıkım",
    )
    # primary_keyword casefold mismatch
    ctx_kw_case = SocialContentGroundingContext(
        primary_keyword="Organik Cilt Bakımı",  # Baş harfler büyük
        product_facts="Dermatolojik Olarak Test Edilmiştir.",
        trusted_brand_usp="Organik Soğuk Sıkım",
    )
    with pytest.raises(SocialContentPromptError) as exc_kw:
        validate_grounding_prompt_parity(ctx_kw_case, inp)
    assert exc_kw.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_kw.value.field == "primary_keyword"

    # product_facts casefold mismatch
    ctx_facts_case = SocialContentGroundingContext(
        primary_keyword="organik cilt bakımı",
        product_facts="dermatolojik olarak test edilmiştir.",  # küçük harf
        trusted_brand_usp="Organik Soğuk Sıkım",
    )
    with pytest.raises(SocialContentPromptError) as exc_facts:
        validate_grounding_prompt_parity(ctx_facts_case, inp)
    assert exc_facts.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_facts.value.field == "product_facts"

    # trusted_brand_usp casefold mismatch
    ctx_usp_case = SocialContentGroundingContext(
        primary_keyword="organik cilt bakımı",
        product_facts="Dermatolojik Olarak Test Edilmiştir.",
        trusted_brand_usp="organik soğuk sıkım",  # küçük harf
    )
    with pytest.raises(SocialContentPromptError) as exc_usp:
        validate_grounding_prompt_parity(ctx_usp_case, inp)
    assert exc_usp.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_usp.value.field == "trusted_brand_usp"


def test_builder_recalculates_authoritative_decision_clean_content_forged_repair():
    """build_social_content_repair_prompt temiz içerik için dışarıdan forged repair verilse dahi otoriter kararı yeniden hesaplayarak CONTENT_REPAIR_NOT_REQUIRED üretir."""
    inp = _make_prompt_input()
    clean_content = _make_video_content(duration_status="valid", caption="Doğal cilt bakım rutini önerileri.")
    context = _make_grounding_context(inp)

    # Otoriter karar 'accept' olacaktır
    authoritative = evaluate_social_content_quality(clean_content, context, repair_attempted=False)
    assert authoritative.action == "accept"

    # Dışarıdan forged repair kararı veriliyor
    forged_decision = SocialContentQualityDecision(
        action="repair",
        reason_codes=("ungrounded_claim",),
        claims=("%80",),
        warnings=(),
        grounding_clean=False,
        duration_acceptable=True,
        repair_attempted=False,
    )

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, clean_content, context, forged_decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_NOT_REQUIRED"


def test_builder_rejects_forged_claims_mismatch():
    """İçerikte duration mismatch varken ve iddia yokken, dışarıdan ungrounded_claim/claims içeren forged karar reddedilir."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)

    # Otoriter kararda claim yok, yalnızca duration_mismatch var
    authoritative = evaluate_social_content_quality(content, context, repair_attempted=False)
    assert authoritative.action == "repair"
    assert authoritative.reason_codes == ("duration_mismatch",)
    assert authoritative.claims == ()

    # Dışarıdan uydurma claim eklenmiş karar veriliyor
    forged_decision = SocialContentQualityDecision(
        action="repair",
        reason_codes=("duration_mismatch",),
        claims=("%80 memnuniyet",),  # Yetkisiz eklenmiş claim
        warnings=(),
        grounding_clean=True,
        duration_acceptable=False,
        repair_attempted=False,
    )

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, content, context, forged_decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_info.value.field == "quality_decision"


def test_builder_rejects_forged_reason_codes_mismatch():
    """Otoriter reason_codes ile dışarıdan verilen reason_codes uyuşmuyorsa CONTENT_REPAIR_INVALID_INPUT döner."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)

    forged_decision = SocialContentQualityDecision(
        action="repair",
        reason_codes=("duration_mismatch", "ungrounded_claim"),  # Yetkisiz eklenmiş ungrounded_claim
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=False,
        repair_attempted=False,
    )

    with pytest.raises(SocialContentPromptError) as exc_info:
        build_social_content_repair_prompt(inp, content, context, forged_decision)

    assert exc_info.value.error_code == "CONTENT_REPAIR_INVALID_INPUT"
    assert exc_info.value.field == "quality_decision"


def test_builder_without_optional_quality_decision():
    """build_social_content_repair_prompt çağrısına quality_decision verilmediğinde otoriter kararı kendisi başarıyla hesaplar."""
    inp = _make_prompt_input()
    content = _make_video_content(duration_status="mismatch", actual_duration_sec=45)
    context = _make_grounding_context(inp)

    # 4. parametre olmadan çağrı
    prompt = build_social_content_repair_prompt(inp, content, context)

    assert "<INPUT_JSON>" in prompt
    assert "</INPUT_JSON>" in prompt
    assert "DÜZELTME TALİMATLARI (SÜRE VE ZAMAN ÇİZELGESİ DÜZELTME)" in prompt
    assert '"reason_codes": [\n      "duration_mismatch"\n    ]' in prompt


def test_positive_parity_all_none_and_exact_strings():
    """Pozitif parite: Tüm opsiyonel alanların None olduğu ve Türkçe/özel karakter içeren exact eşleşmelerde başarıyla tek çağrı yapılır."""
    # Vaka A: product_facts ve trusted_brand_usp her iki DTO'da da None
    inp_none = _make_prompt_input(
        primary_keyword=SocialContentKeywordInput(1, "doğal sabun"),
        product_facts=None,
        trusted_brand_usp=None,
    )
    ctx_none = _make_grounding_context(inp_none)
    content_a = _make_video_content(duration_status="mismatch", actual_duration_sec=45)

    resp_dict = _make_valid_video_response_dict(end_sec=22)
    fake_ai_a = FakeAIService(responses=[json.dumps(resp_dict)])
    gen_a = SocialBriefContentGenerator(fake_ai_a)

    res_a = gen_a.repair_once(inp_none, content_a, ctx_none)
    assert res_a.ai_calls_used == 1
    assert fake_ai_a.call_count == 1
    assert res_a.quality_decision.action == "accept"

    # Vaka B: Türkçe ve XML özel karakterli tam eşleşme
    inp_tr = _make_prompt_input(
        primary_keyword=SocialContentKeywordInput(2, "şifalı & organik bitki çayı"),
        product_facts="İçerik %100 doğaldır & dermatolojik onaylıdır.",
        trusted_brand_usp="Özel Şifa Formülü & Kalite",
    )
    ctx_tr = _make_grounding_context(inp_tr)
    content_b = _make_video_content(duration_status="mismatch", actual_duration_sec=45)

    fake_ai_b = FakeAIService(responses=[json.dumps(resp_dict)])
    gen_b = SocialBriefContentGenerator(fake_ai_b)

    res_b = gen_b.repair_once(inp_tr, content_b, ctx_tr)
    assert res_b.ai_calls_used == 1
    assert fake_ai_b.call_count == 1
    assert res_b.quality_decision.action == "accept"

    # Prompt içinde XML escaping ve exact alanların varlığı doğrulanır
    prompt = build_social_content_repair_prompt(inp_tr, content_b, ctx_tr)
    assert prompt.count("<INPUT_JSON>") == 1
    assert prompt.count("</INPUT_JSON>") == 1
    assert "\\u0026" in prompt
    assert "şifalı" in prompt
