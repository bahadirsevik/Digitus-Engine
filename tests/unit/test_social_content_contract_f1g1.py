# -*- coding: utf-8 -*-
"""Mikro Faz F1-G.1 Unit Testleri: Saf Formata Özel İçerik Sözleşmesi ve Süre Doğrulayıcısı."""
from __future__ import annotations

import dataclasses
import json
import pytest

from app.core.social.content_contract import (
    ALLOWED_HOOK_STYLES,
    CONTENT_CONTRACT_INVALID_DURATION,
    CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
    CONTENT_CONTRACT_INVALID_HASHTAGS,
    CONTENT_CONTRACT_INVALID_INPUT,
    CONTENT_CONTRACT_INVALID_TARGET,
    CONTENT_CONTRACT_INVALID_TEXT,
    CONTENT_CONTRACT_INVALID_TIMELINE,
    ContentTargetSpec,
    SocialContentOutputValidationError,
    VOICEOVER_MAX_WORDS_PER_SEC,
    VOICEOVER_MIN_WORDS_PER_SEC,
    VOICEOVER_WARNING_REASON_CODE,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedThreadPayload,
    ValidatedThreadPost,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
    build_social_content_response_schema,
    render_legacy_scenario,
    serialize_content_format_payload,
    validate_content_target_spec,
    validate_social_content_output,
)
from app.generators.social.format_matrix import (
    CANONICAL_PLATFORMS,
    get_platform_format,
)


def _build_valid_payload_for_format(fmt: str, duration_sec: int = 58) -> dict | None:
    """Belirtilen formata göre geçerli bir format_payload döner."""
    if fmt in ("video", "reels", "short"):
        if duration_sec <= 1:
            segments = [
                {
                    "start_sec": 0,
                    "end_sec": 1,
                    "scene": "Açılış kancası ve dikkat çekici görsel",
                    "on_screen_text": "Dikkat! Önemli Bilgi",
                    "voiceover": "Bugün sizlere çok önemli bir konudan bahsedeceğiz.",
                }
            ]
        else:
            mid = max(1, duration_sec // 2)
            segments = [
                {
                    "start_sec": 0,
                    "end_sec": mid,
                    "scene": "Açılış kancası ve dikkat çekici görsel",
                    "on_screen_text": "Dikkat! Önemli Bilgi",
                    "voiceover": "Bugün sizlere çok önemli bir konudan bahsedeceğiz.",
                },
                {
                    "start_sec": mid,
                    "end_sec": duration_sec,
                    "scene": "Detaylı çözüm ve çağrı",
                    "on_screen_text": "Hemen Takip Et",
                    "voiceover": "Detaylar için profildeki linke göz atmayı unutmayın.",
                },
            ]
        return {
            "kind": "video",
            "segments": segments,
        }
    elif fmt == "carousel":
        return {
            "kind": "carousel",
            "slides": [
                {
                    "position": 1,
                    "headline": "Kapak Başlığı",
                    "body": "Slaytın merak uyandıran giriş metni.",
                    "visual_direction": "Koyu zemin üzerine kontrast tipografi.",
                },
                {
                    "position": 2,
                    "headline": "Çözüm ve Detay",
                    "body": "Adım adım rehberin detaylı açıklaması.",
                    "visual_direction": "İnfografik ve ikonik anlatım.",
                },
            ],
        }
    elif fmt == "thread":
        return {
            "kind": "thread",
            "posts": [
                {"position": 1, "text": "Bu bir thread başlangıç tweetidir. Merak uyandırıcıdır."},
                {"position": 2, "text": "İkinci tweet detayları ve stratejiyi açıklar."},
            ],
        }
    else:
        # post veya story
        return None


def _build_valid_content_dict(
    target_spec: ContentTargetSpec,
    *,
    caption: str = "Harika bir sosyal medya içeriği.",
    duration_sec: int = 58,
) -> dict:
    """Hedef spesifikasyonuna uygun geçerli bir ham içerik sözlüğü üretir."""
    payload = _build_valid_payload_for_format(target_spec.content_format, duration_sec=duration_sec)
    return {
        "hooks": [
            {"text": "Bunu daha önce duymuş muydunuz?", "style": "curiosity", "ab_score": 0.85},
            {"text": "Sosyal medyada yapılan en büyük 3 hata!", "style": "shocking", "ab_score": None},
        ],
        "caption": caption,
        "cta_text": "Fikirlerinizi yorumlarda paylaşın!",
        "hashtags": ["dijitalpazarlama", "sosyalmedya", "icerikuretici", "trendler", "buyume"],
        "format_payload": payload,
        "visual_suggestion": "Minimalist ve net tasarım.",
        "video_concept": "Hızlı geçişli B-roll çekimler.",
        "industry_posting_suggestion": "Salı günleri 10:00 - 12:00 arası.",
        "platform_notes": "Yorumlara ilk 30 dakikada yanıt verilmesi önerilir.",
    }


# ==================== 1. HER CANONICAL PLATFORM/FORMAT İÇİN GEÇERLİ ÇIKTI ====================

def test_all_canonical_platform_format_targets_succeed():
    """Tüm kanonik platform ve format kombinasyonları başarıyla doğrulanmalıdır."""
    target_id_counter = 1
    for plat in CANONICAL_PLATFORMS:
        for fmt in plat.formats:
            if fmt.requires_duration:
                preset = fmt.duration_presets[0]
                spec = ContentTargetSpec(
                    target_id=target_id_counter,
                    platform=plat.id,
                    content_format=fmt.id,
                    duration_preset_id=preset.id,
                    duration_min_sec=preset.min_sec,
                    duration_max_sec=preset.max_sec,
                )
                duration_val = preset.max_sec  # valid duration
            else:
                spec = ContentTargetSpec(
                    target_id=target_id_counter,
                    platform=plat.id,
                    content_format=fmt.id,
                )
                duration_val = 0

            target_id_counter += 1
            data = _build_valid_content_dict(spec, duration_sec=duration_val if fmt.requires_duration else 58)
            raw_json = json.dumps(data)

            validated = validate_social_content_output(raw_json, spec)
            assert isinstance(validated, ValidatedSocialContent)
            assert validated.caption == data["caption"]
            assert len(validated.hooks) == 2
            assert len(validated.hashtags) == 5

            if fmt.requires_duration:
                assert validated.duration_status == "valid"
                assert validated.actual_duration_sec == duration_val
                assert validated.format_payload is not None
                assert validated.scenario is not None
            else:
                assert validated.duration_status == "not_applicable"
                assert validated.actual_duration_sec is None
                if fmt.id in ("carousel", "thread"):
                    assert validated.format_payload is not None
                    assert validated.scenario is not None
                else:
                    assert validated.format_payload is None
                    assert validated.scenario is None


# ==================== 2-6. VİDEO TIMELINE VE SEGMENT KONTROLLERİ ====================

@pytest.fixture
def reels_spec() -> ContentTargetSpec:
    return ContentTargetSpec(
        target_id=1,
        platform="instagram",
        content_format="reels",
        duration_preset_id="short_31_60",
        duration_min_sec=31,
        duration_max_sec=60,
    )


def test_video_first_segment_not_zero_fails(reels_spec):
    """İlk segment 0'dan başlamıyorsa CONTENT_CONTRACT_INVALID_TIMELINE fırlatmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"][0]["start_sec"] = 2
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert exc.value.field == "start_sec"
    assert exc.value.item_index == 0


def test_video_segment_gap_fails(reels_spec):
    """Segmentler arasında boşluk (gap) varsa CONTENT_CONTRACT_INVALID_TIMELINE fırlatmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    seg0_end = data["format_payload"]["segments"][0]["end_sec"]
    data["format_payload"]["segments"][1]["start_sec"] = seg0_end + 1
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert exc.value.field == "start_sec"
    assert exc.value.item_index == 1


def test_video_segment_overlap_fails(reels_spec):
    """Segmentler arasında çakışma (overlap) varsa CONTENT_CONTRACT_INVALID_TIMELINE fırlatmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    seg0_end = data["format_payload"]["segments"][0]["end_sec"]
    data["format_payload"]["segments"][1]["start_sec"] = seg0_end - 1
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert exc.value.field == "start_sec"


def test_video_segment_end_sec_not_greater_than_start_sec_fails(reels_spec):
    """end_sec <= start_sec ise CONTENT_CONTRACT_INVALID_TIMELINE fırlatmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"][0]["end_sec"] = 0
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert exc.value.field == "end_sec"


def test_video_empty_segments_fails(reels_spec):
    """Boş segments listesi CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD ile reddedilmelidir."""
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"] = []
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


# ==================== 7-10. PRESET SÜRE VALID / MISMATCH KONTROLLERİ ====================

def test_preset_duration_valid_at_58s(reels_spec):
    """31–60 presetinde 58 saniye geçerli süre durumudur (valid)."""
    data = _build_valid_content_dict(reels_spec, duration_sec=58)
    validated = validate_social_content_output(json.dumps(data), reels_spec)

    assert validated.duration_status == "valid"
    assert validated.actual_duration_sec == 58


def test_preset_duration_mismatch_at_61s(reels_spec):
    """31–60 presetinde 61 saniye yapısal olarak geçerli kalır ancak duration_status='mismatch' olur."""
    data = _build_valid_content_dict(reels_spec, duration_sec=61)
    validated = validate_social_content_output(json.dumps(data), reels_spec)

    assert validated.duration_status == "mismatch"
    assert validated.actual_duration_sec == 61


def test_preset_duration_mismatch_below_min_at_30s(reels_spec):
    """31–60 presetinde 30 saniye mismatch olarak işaretlenir."""
    data = _build_valid_content_dict(reels_spec, duration_sec=30)
    validated = validate_social_content_output(json.dumps(data), reels_spec)

    assert validated.duration_status == "mismatch"
    assert validated.actual_duration_sec == 30


# ==================== 11-17. CAROUSEL, THREAD, POST VE STORY KONTROLLERİ ====================

@pytest.fixture
def carousel_spec() -> ContentTargetSpec:
    return ContentTargetSpec(
        target_id=2,
        platform="instagram",
        content_format="carousel",
    )


@pytest.fixture
def thread_spec() -> ContentTargetSpec:
    return ContentTargetSpec(
        target_id=3,
        platform="twitter",
        content_format="thread",
    )


@pytest.fixture
def post_spec() -> ContentTargetSpec:
    return ContentTargetSpec(
        target_id=4,
        platform="instagram",
        content_format="post",
    )


@pytest.fixture
def story_spec() -> ContentTargetSpec:
    return ContentTargetSpec(
        target_id=5,
        platform="instagram",
        content_format="story",
    )


def test_video_payload_rejected_for_carousel_target(carousel_spec):
    """Carousel hedefine video payload gönderilirse reddedilir."""
    data = _build_valid_content_dict(carousel_spec)
    data["format_payload"] = _build_valid_payload_for_format("video")
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, carousel_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


def test_empty_slides_rejected(carousel_spec):
    """Boş slides listesi reddedilir."""
    data = _build_valid_content_dict(carousel_spec)
    data["format_payload"]["slides"] = []
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, carousel_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


def test_slide_position_discontinuity_rejected(carousel_spec):
    """Slide position sıralaması 1'den başlamazsa veya kesintiliyse reddedilir."""
    data = _build_valid_content_dict(carousel_spec)
    # Slayt 1 ve Slayt 3 (2 atlandı)
    data["format_payload"]["slides"][1]["position"] = 3
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, carousel_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD
    assert exc.value.field == "position"


def test_empty_posts_rejected(thread_spec):
    """Boş thread posts listesi reddedilir."""
    data = _build_valid_content_dict(thread_spec)
    data["format_payload"]["posts"] = []
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, thread_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


def test_thread_post_over_280_chars_rejected(thread_spec):
    """Thread tweeti 280 karakteri aşarsa CONTENT_CONTRACT_INVALID_TEXT ile reddedilir."""
    data = _build_valid_content_dict(thread_spec)
    data["format_payload"]["posts"][0]["text"] = "T" * 281
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, thread_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT
    assert exc.value.field == "posts.text"


def test_post_with_payload_rejected(post_spec):
    """Post formatı payload taşırsa reddedilir."""
    data = _build_valid_content_dict(post_spec)
    data["format_payload"] = {"kind": "carousel", "slides": []}
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


def test_story_with_payload_rejected(story_spec):
    """Statik story formatı payload taşırsa reddedilir."""
    data = _build_valid_content_dict(story_spec)
    data["format_payload"] = {"kind": "video", "segments": []}
    raw = json.dumps(data)

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, story_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


def test_static_story_has_not_applicable_duration(story_spec):
    """Statik Story video gibi değerlendirilmez; duration_status='not_applicable' olur."""
    data = _build_valid_content_dict(story_spec)
    validated = validate_social_content_output(json.dumps(data), story_spec)

    assert validated.duration_status == "not_applicable"
    assert validated.actual_duration_sec is None
    assert validated.format_payload is None
    assert validated.scenario is None


# ==================== 18-19. CONTENT TARGET SPEC DOĞRULAMALARI ====================

def test_video_target_spec_missing_duration_rejected():
    """Video formatı için duration bilgileri eksikse reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        ContentTargetSpec(
            target_id=1,
            platform="instagram",
            content_format="reels",
            duration_preset_id=None,
        )
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION


def test_video_target_spec_invalid_preset_for_profile_rejected():
    """Video preset'i o formata ait değilse reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        ContentTargetSpec(
            target_id=1,
            platform="instagram",
            content_format="reels",
            duration_preset_id="long_60_180",  # reels short_video ister
            duration_min_sec=60,
            duration_max_sec=180,
        )
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION


def test_video_target_spec_min_max_mismatch_rejected():
    """Preset'in kanonik min/max değerleriyle eşleşmeyen spec reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        ContentTargetSpec(
            target_id=1,
            platform="instagram",
            content_format="reels",
            duration_preset_id="short_31_60",
            duration_min_sec=30,  # 31 olmalı
            duration_max_sec=60,
        )
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION


def test_non_video_target_spec_with_duration_rejected():
    """Non-video format (post/carousel) duration preset taşırsa reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        ContentTargetSpec(
            target_id=1,
            platform="instagram",
            content_format="post",
            duration_preset_id="short_1_15",
            duration_min_sec=1,
            duration_max_sec=15,
        )
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION


# ==================== 20. CAPTION PLATFORM SINIRLARI ====================

@pytest.mark.parametrize(
    "platform,fmt,max_chars",
    [
        ("instagram", "post", 2200),
        ("tiktok", "short", 2200),
        ("twitter", "post", 280),
        ("linkedin", "post", 3000),
        ("youtube", "video", 5000),
    ],
)
def test_caption_platform_limits(platform, fmt, max_chars):
    """Her platform için sınırda geçerli, 1 karakter aşımında geçersiz olmalıdır."""
    fmt_def = get_platform_format(platform, fmt)
    preset = fmt_def.duration_presets[0] if fmt_def.requires_duration else None

    spec = ContentTargetSpec(
        target_id=1,
        platform=platform,
        content_format=fmt,
        duration_preset_id=preset.id if preset else None,
        duration_min_sec=preset.min_sec if preset else None,
        duration_max_sec=preset.max_sec if preset else None,
    )

    # Tam sınırda geçerli
    valid_data = _build_valid_content_dict(
        spec,
        caption="A" * max_chars,
        duration_sec=preset.max_sec if preset else 58,
    )
    validated = validate_social_content_output(json.dumps(valid_data), spec)
    assert len(validated.caption) == max_chars

    # 1 karakter fazla -> reddedilmeli
    invalid_data = _build_valid_content_dict(
        spec,
        caption="A" * (max_chars + 1),
        duration_sec=preset.max_sec if preset else 58,
    )
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(invalid_data), spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT
    assert exc.value.field == "caption"


# ==================== 21. HOOK KONTROLLERİ ====================

def test_hook_text_and_style_validations(post_spec):
    """Hook text ve style sınırları doğrulanmalıdır."""
    data = _build_valid_content_dict(post_spec)
    data["hooks"] = []
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT

    # Geçersiz stil
    data = _build_valid_content_dict(post_spec)
    data["hooks"][0]["style"] = "unknown_style"
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT
    assert exc.value.field == "hooks.style"

    # 500 karakteri aşan hook
    data = _build_valid_content_dict(post_spec)
    data["hooks"][0]["text"] = "H" * 501
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT
    assert exc.value.field == "hooks.text"


def test_hook_ab_score_validations(post_spec):
    """ab_score için bool, negatif, >1.0, NaN ve taşma değerleri reddedilir."""
    # bool reddedilir
    data = _build_valid_content_dict(post_spec)
    data["hooks"][0]["ab_score"] = True
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT

    # > 1.0 reddedilir
    data["hooks"][0]["ab_score"] = 1.05
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT

    # Negatif reddedilir
    data["hooks"][0]["ab_score"] = -0.1
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT


# ==================== 22. HASHTAG KONTROLLERİ ====================

def test_hashtag_count_and_format_validations(post_spec):
    """Hashtag sayısı (5-20), # işareti olmaması ve duplicate kontrolü."""
    # 4 hashtag (yetersiz)
    data = _build_valid_content_dict(post_spec)
    data["hashtags"] = ["bir", "iki", "uc", "dort"]
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_HASHTAGS

    # 21 hashtag (fazla)
    data["hashtags"] = [f"tag_{i}" for i in range(21)]
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_HASHTAGS

    # # ile başlayan etiket
    data["hashtags"] = ["#hatali", "etiket", "deneme", "test", "dogru"]
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_HASHTAGS

    # Tekrarlanan (duplicate) etiket
    data["hashtags"] = ["ayni", "ayni", "deneme", "test", "dogru"]
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_HASHTAGS


# ==================== 23. BOOL, NUMERIC STRING, NAN, INF, 10**10000 ====================

def test_type_coercion_and_unsafe_numbers_rejected(reels_spec):
    """Sayısal stringler sayıya çevrilmez; bool, NaN, Infinity reddedilir."""
    # start_sec için boolean True
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"][0]["start_sec"] = True
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE

    # start_sec için sayısal string "0"
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"][0]["start_sec"] = "0"
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE

    # NaN ve Infinity JSON stringinde
    raw_nan = '{"caption": "test", "cta_text": "cta", "hooks": [], "hashtags": [], "format_payload": null, "extra": NaN}'
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw_nan, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT

    # 10**10000 büyük sayı
    raw_big = '{"caption": "test", "num": ' + ("9" * 5000) + "}"
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw_big, reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT


# ==================== 24-25. BİLİNMEYEN VE EKSİK ALANLAR ====================

def test_unexpected_root_fields_rejected(post_spec):
    """Root JSON'da beklenmeyen alan (ör. scenario) bulunursa reddedilir."""
    data = _build_valid_content_dict(post_spec)
    data["scenario"] = "Yapay zekanın ürettiği ayrı senaryo alanı"
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT
    assert exc.value.field == "scenario"


def test_missing_required_fields_rejected(post_spec):
    """Zorunlu alan eksikse reddedilir."""
    data = _build_valid_content_dict(post_spec)
    del data["caption"]
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT
    assert exc.value.field == "caption"


# ==================== 26-27. MARKDOWN FENCE VE DUPLICATE JSON KEY ====================

def test_markdown_fence_recovered_and_accepted(post_spec):
    """Markdown kod bloku içine alınmış JSON artık TEK kurtarma denemesiyle kabul edilir.

    GÜNCELLEME (CLAUDE.md §11): önceki davranış (fail-closed reddet) katı bare
    json.loads tuzağıydı; artık app/core/channel/ai_json.py kurtarma zincirinden
    TEK deneme yapılır ve fence temizlenmiş içerik normal şekilde doğrulanır.
    """
    data = _build_valid_content_dict(post_spec)
    raw = f"```json\n{json.dumps(data)}\n```"
    result = validate_social_content_output(raw, post_spec)
    assert result.caption == data["caption"]


def test_markdown_fence_with_recovery_still_invalid_rejected(post_spec):
    """Fence kurtarıldıktan sonra içerik hâlâ geçersizse (örn. eksik zorunlu alan) reddedilir."""
    raw = "```json\n{\"caption\": \"eksik alanlar\"}\n```"
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT


def test_text_fields_stripped_not_rejected_for_surrounding_whitespace(post_spec):
    """Metin alanlarındaki baştan/sondan boşluk artık reddedilmez, kırpılıp kabul edilir.

    GÜNCELLEME (CLAUDE.md §11): önceki davranış her metin alanını (caption,
    cta_text, hooks.text, hashtags, opsiyonel alanlar) baştan/sondan boşluk
    içeriyorsa katı biçimde reddediyordu. Artık strip edilir; yalnızca
    kırpma sonrası boş kalan metin reddedilir.
    """
    data = _build_valid_content_dict(post_spec, caption="  Boşluklu caption.  ")
    data["cta_text"] = "  Tıkla  "
    data["hooks"][0]["text"] = "  Boşluklu hook  "
    data["hashtags"][0] = "  boslukluetiket  "
    data["visual_suggestion"] = "  Boşluklu öneri  "

    result = validate_social_content_output(json.dumps(data), post_spec)

    assert result.caption == "Boşluklu caption."
    assert result.cta_text == "Tıkla"
    assert result.hooks[0].text == "Boşluklu hook"
    assert result.hashtags[0] == "boslukluetiket"
    assert result.visual_suggestion == "Boşluklu öneri"


def test_whitespace_only_text_field_still_rejected(post_spec):
    """Kırpma sonrası tamamen boş kalan metin alanı hâlâ reddedilir."""
    data = _build_valid_content_dict(post_spec, caption="   ")
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TEXT
    assert exc.value.field == "caption"


def test_leading_trailing_prose_still_rejected_no_fence(post_spec):
    """Fence OLMADAN leading/trailing prose içeren metin kurtarılmaya çalışılmaz, reddedilir."""
    data = _build_valid_content_dict(post_spec)
    raw = "İşte içerik: " + json.dumps(data)
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT


def test_duplicate_json_key_rejected(post_spec):
    """Aynı JSON anahtarının tekrarlanması fail-closed reddedilir."""
    raw = (
        '{"caption": "ilk", "caption": "ikinci", "cta_text": "cta", '
        '"hooks": [{"text": "h", "style": "curiosity"}], '
        '"hashtags": ["a", "b", "c", "d", "e"], "format_payload": null}'
    )
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(raw, post_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT


# ==================== 28. RESPONSE SCHEMA DEEP-COPY IMMUTABILITY ====================

def test_response_schema_deep_copy_immutability(reels_spec):
    """Dönen response schema'nın mutate edilmesi bir sonraki çağrıyı etkilememelidir."""
    s1 = build_social_content_response_schema(reels_spec)
    s1["properties"]["caption"]["description"] = "MUTATED"
    s1["required"].append("new_field")

    s2 = build_social_content_response_schema(reels_spec)
    assert s2["properties"]["caption"]["description"] != "MUTATED"
    assert "new_field" not in s2["required"]


# ==================== 29. LEGACY SCENARIO DETERMINİSTİK ÜRETİM ====================

def test_legacy_scenario_rendering(reels_spec, carousel_spec, thread_spec, post_spec):
    """Format payload'larından okunabilir düz metin senaryosu deterministik üretilir."""
    # Video
    v_data = _build_valid_content_dict(reels_spec)
    v_content = validate_social_content_output(json.dumps(v_data), reels_spec)
    assert v_content.scenario is not None
    assert "[00-" in v_content.scenario
    assert "Sahne:" in v_content.scenario
    assert "Ses:" in v_content.scenario
    assert render_legacy_scenario(v_content) == v_content.scenario

    # Carousel
    c_data = _build_valid_content_dict(carousel_spec)
    c_content = validate_social_content_output(json.dumps(c_data), carousel_spec)
    assert c_content.scenario is not None
    assert "[Slide 1] Kapak Başlığı" in c_content.scenario

    # Thread
    t_data = _build_valid_content_dict(thread_spec)
    t_content = validate_social_content_output(json.dumps(t_data), thread_spec)
    assert t_content.scenario is not None
    assert "[1] Bu bir thread başlangıç tweetidir." in t_content.scenario

    # Post
    p_data = _build_valid_content_dict(post_spec)
    p_content = validate_social_content_output(json.dumps(p_data), post_spec)
    assert p_content.scenario is None
    assert render_legacy_scenario(p_content) is None


# ==================== 30. VOICEOVER SÜRE UYARISI ====================

def test_voiceover_duration_mismatch_warning_only(reels_spec):
    """Voiceover kelime sayısı süreyle aşırı uyumsuzsa yalnız warning üretir; duration_status bozulmaz."""
    data = _build_valid_content_dict(reels_spec, duration_sec=35)
    # 35 saniyelik videoda 200 kelimelik seslendirme (~5.7 kelime/sn > 3.5)
    data["format_payload"]["segments"][1]["voiceover"] = " ".join(["kelime"] * 200)

    validated = validate_social_content_output(json.dumps(data), reels_spec)
    assert validated.duration_status == "valid"
    assert VOICEOVER_WARNING_REASON_CODE in validated.validation_warnings
    # Uyarı metninde ham seslendirme içeriği bulunmamalıdır
    assert all(w == VOICEOVER_WARNING_REASON_CODE for w in validated.validation_warnings)


# ==================== 31. HATA MESAJLARINDA SENSITIVE METİN SIZMAMASI ====================

def test_error_messages_contain_no_raw_text_or_ids(post_spec):
    """Hata mesajlarında caption, hook, hashtag veya kullanıcı metni sızmamalıdır.

    GÜNCELLEME (CLAUDE.md §11): baştan/sondan boşluk artık kırpılıp kabul
    edildiği için (bkz. test_text_fields_stripped_not_rejected_for_surrounding_whitespace)
    hata tetikleyici olarak whitespace-only (kırpma sonrası boş) caption kullanılır.
    """
    secret_caption = "SECRET_SUPER_CONFIDENTIAL_CAPTION_TEXT_123"
    data = _build_valid_content_dict(post_spec, caption=secret_caption)
    # Kırpma sonrası tamamen boş kalacak şekilde hata tetikle
    data["caption"] = "   "

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), post_spec)

    err_str = str(exc.value)
    assert secret_caption not in err_str


# ==================== 32. VALIDATED DTO IMMUTABILITY ====================

def test_validated_dtos_are_frozen(post_spec):
    """ValidatedSocialContent ve ContentTargetSpec immutable (frozen) olmalıdır."""
    data = _build_valid_content_dict(post_spec)
    content = validate_social_content_output(json.dumps(data), post_spec)

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.caption = "yeni_caption"

    with pytest.raises(dataclasses.FrozenInstanceError):
        post_spec.platform = "tiktok"


# ==================== 33. HARİCİ BAĞIMLILIK DENETİMİ ====================

def test_no_forbidden_dependencies():
    """content_contract modülü DB, ORM, Pydantic veya AI SDK'larına bağımlı olmamalıdır."""
    import sys
    import app.core.social.content_contract as cc

    forbidden_modules = [
        "sqlalchemy",
        "pydantic",
        "google.generativeai",
        "requests",
        "httpx",
    ]
    for mod in forbidden_modules:
        assert mod not in cc.__dict__, f"{mod} must not be directly imported in content_contract"


# ==================== 34. DERİN IMMUTABILITY TESTLERİ (F1-G.1a) ====================

def test_deep_immutability_video_payload(reels_spec):
    """Video payload ve nested segmentler derinlemesine değiştirilemez olmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    content = validate_social_content_output(json.dumps(data), reels_spec)

    assert isinstance(content.format_payload, ValidatedVideoPayload)
    assert isinstance(content.format_payload.segments, tuple)
    assert isinstance(content.format_payload.segments[0], ValidatedVideoSegment)

    # 1. Payload kind değiştirilemez
    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.kind = "thread"  # type: ignore

    # 2. Segment süreleri değiştirilemez
    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.segments[0].start_sec = 5  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.segments[0].end_sec = 999  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.segments[0].scene = "yeni sahne"  # type: ignore

    # 3. Segments tuple'ına ekleme yapılamaz
    with pytest.raises(AttributeError):
        content.format_payload.segments.append(content.format_payload.segments[0])  # type: ignore


def test_deep_immutability_carousel_payload(carousel_spec):
    """Carousel payload ve nested slide'lar derinlemesine değiştirilemez olmalıdır."""
    data = _build_valid_content_dict(carousel_spec)
    content = validate_social_content_output(json.dumps(data), carousel_spec)

    assert isinstance(content.format_payload, ValidatedCarouselPayload)
    assert isinstance(content.format_payload.slides, tuple)
    assert isinstance(content.format_payload.slides[0], ValidatedCarouselSlide)

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.kind = "video"  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.slides[0].headline = "yeni başlık"  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.slides[0].position = 99  # type: ignore

    with pytest.raises(AttributeError):
        content.format_payload.slides.append(content.format_payload.slides[0])  # type: ignore


def test_deep_immutability_thread_payload(thread_spec):
    """Thread payload ve nested post'lar derinlemesine değiştirilemez olmalıdır."""
    data = _build_valid_content_dict(thread_spec)
    content = validate_social_content_output(json.dumps(data), thread_spec)

    assert isinstance(content.format_payload, ValidatedThreadPayload)
    assert isinstance(content.format_payload.posts, tuple)
    assert isinstance(content.format_payload.posts[0], ValidatedThreadPost)

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.kind = "carousel"  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        content.format_payload.posts[0].text = "yeni tweet"  # type: ignore

    with pytest.raises(AttributeError):
        content.format_payload.posts.append(content.format_payload.posts[0])  # type: ignore


def test_no_mutable_containers_in_validated_social_content(reels_spec, carousel_spec, thread_spec):
    """ValidatedSocialContent ağacında hiçbir mutable dict/list/set bulunmamalıdır."""
    def _assert_no_mutable(obj, path="root"):
        assert not isinstance(obj, (dict, list, set)), f"Mutable container {type(obj)} found at {path}"
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                val = getattr(obj, f.name)
                _assert_no_mutable(val, f"{path}.{f.name}")
        elif isinstance(obj, (tuple, frozenset)):
            for idx, item in enumerate(obj):
                _assert_no_mutable(item, f"{path}[{idx}]")

    for spec in (reels_spec, carousel_spec, thread_spec):
        data = _build_valid_content_dict(spec)
        content = validate_social_content_output(json.dumps(data), spec)
        _assert_no_mutable(content)


# ==================== 35. JSON PERSISTENCE SERIALIZER TESTLERİ (F1-G.1a) ====================

def test_serializer_outputs_canonical_shape(reels_spec, carousel_spec, thread_spec, post_spec):
    """Serializer tüm DTO türlerini doğru ve eksiksiz canonical dict/list yapısına çevirmelidir."""
    # Video
    v_data = _build_valid_content_dict(reels_spec)
    v_content = validate_social_content_output(json.dumps(v_data), reels_spec)
    v_serialized = serialize_content_format_payload(v_content.format_payload)

    assert isinstance(v_serialized, dict)
    assert v_serialized["kind"] == "video"
    assert isinstance(v_serialized["segments"], list)
    assert len(v_serialized["segments"]) == len(v_content.format_payload.segments)
    assert v_serialized["segments"][0]["start_sec"] == 0
    assert "voiceover" in v_serialized["segments"][0]

    # Carousel
    c_data = _build_valid_content_dict(carousel_spec)
    c_content = validate_social_content_output(json.dumps(c_data), carousel_spec)
    c_serialized = serialize_content_format_payload(c_content.format_payload)

    assert isinstance(c_serialized, dict)
    assert c_serialized["kind"] == "carousel"
    assert isinstance(c_serialized["slides"], list)
    assert c_serialized["slides"][0]["position"] == 1
    assert "visual_direction" in c_serialized["slides"][0]

    # Thread
    t_data = _build_valid_content_dict(thread_spec)
    t_content = validate_social_content_output(json.dumps(t_data), thread_spec)
    t_serialized = serialize_content_format_payload(t_content.format_payload)

    assert isinstance(t_serialized, dict)
    assert t_serialized["kind"] == "thread"
    assert isinstance(t_serialized["posts"], list)
    assert t_serialized["posts"][0]["position"] == 1

    # Post / Story (None)
    p_data = _build_valid_content_dict(post_spec)
    p_content = validate_social_content_output(json.dumps(p_data), post_spec)
    assert serialize_content_format_payload(p_content.format_payload) is None
    assert serialize_content_format_payload(None) is None


def test_serializer_mutating_output_does_not_affect_dto(reels_spec):
    """Serializer'ın döndürdüğü dict mutate edilirse DTO etkilenmemelidir."""
    v_data = _build_valid_content_dict(reels_spec)
    content = validate_social_content_output(json.dumps(v_data), reels_spec)

    serialized = serialize_content_format_payload(content.format_payload)
    serialized["kind"] = "MUTATED"
    serialized["segments"][0]["start_sec"] = 999
    serialized["segments"].clear()

    # DTO orijinal değerlerini korur
    assert content.format_payload.kind == "video"
    assert content.format_payload.segments[0].start_sec == 0
    assert len(content.format_payload.segments) > 0


def test_serializer_repeated_calls_return_independent_objects(reels_spec):
    """İki ayrı serializer çağrısı birbirinden bağımsız yeni nesneler üretir."""
    v_data = _build_valid_content_dict(reels_spec)
    content = validate_social_content_output(json.dumps(v_data), reels_spec)

    s1 = serialize_content_format_payload(content.format_payload)
    s2 = serialize_content_format_payload(content.format_payload)

    assert s1 == s2
    assert s1 is not s2
    assert s1["segments"] is not s2["segments"]


def test_serializer_rejects_forged_payload_type():
    """Bilinmeyen veya sahte nesne tipi fail-closed reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        serialize_content_format_payload({"kind": "video", "segments": []})  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD

    with pytest.raises(SocialContentOutputValidationError) as exc:
        serialize_content_format_payload("not_a_payload")  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD


# ==================== 36. SCENARIO API SERTLEŞTİRMESİ TESTLERİ (F1-G.1a) ====================

def test_scenario_api_rejects_raw_types_and_none(reels_spec):
    """render_legacy_scenario yalnız ValidatedSocialContent kabul eder; dict/list/None reddedilir."""
    with pytest.raises(SocialContentOutputValidationError) as exc:
        render_legacy_scenario({"kind": "video", "segments": []})  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT

    with pytest.raises(SocialContentOutputValidationError) as exc:
        render_legacy_scenario([])  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT

    with pytest.raises(SocialContentOutputValidationError) as exc:
        render_legacy_scenario(None)  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT

    with pytest.raises(SocialContentOutputValidationError) as exc:
        render_legacy_scenario("string_content")  # type: ignore
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_INPUT


# ==================== 37. TIMELINE DEV TAMSAYI VE CANONICAL ÜST SINIR TESTLERİ (F1-G.1a) ====================

def test_timeline_negative_start_sec_rejected(reels_spec):
    """start_sec < 0 olduğunda CONTENT_CONTRACT_INVALID_TIMELINE fırlatmalıdır."""
    data = _build_valid_content_dict(reels_spec)
    data["format_payload"]["segments"][0]["start_sec"] = -1
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_TIMELINE
    assert exc.value.field == "start_sec"


def test_timeline_reels_91s_exceeds_canonical_profile_ceiling_rejected(reels_spec):
    """Reels için canonical short_video tavanı 90s'dir; 91 saniye CONTENT_CONTRACT_INVALID_DURATION ile reddedilir."""
    data = _build_valid_content_dict(reels_spec, duration_sec=91)
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), reels_spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION
    assert exc.value.field == "end_sec"


def test_timeline_x_video_141s_exceeds_canonical_profile_ceiling_rejected():
    """X video için canonical tavan 140s'dir; 141 saniye reddedilir."""
    spec = ContentTargetSpec(
        target_id=10,
        platform="twitter",
        content_format="video",
        duration_preset_id="x_91_140",
        duration_min_sec=91,
        duration_max_sec=140,
    )
    data = _build_valid_content_dict(spec, duration_sec=141)
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION
    assert exc.value.field == "end_sec"


def test_timeline_linkedin_video_601s_exceeds_canonical_profile_ceiling_rejected():
    """LinkedIn video için canonical tavan 600s'dir; 601 saniye reddedilir."""
    spec = ContentTargetSpec(
        target_id=11,
        platform="linkedin",
        content_format="video",
        duration_preset_id="long_301_600",
        duration_min_sec=301,
        duration_max_sec=600,
    )
    data = _build_valid_content_dict(spec, duration_sec=601)
    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(json.dumps(data), spec)
    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION
    assert exc.value.field == "end_sec"


def test_timeline_preset_outside_but_within_profile_is_mismatch(reels_spec):
    """Seçilen preset 31-60 iken 61 saniye profil tavanını (90) aşmaz; duration_status='mismatch' olur."""
    data = _build_valid_content_dict(reels_spec, duration_sec=61)
    content = validate_social_content_output(json.dumps(data), reels_spec)
    assert content.duration_status == "mismatch"
    assert content.actual_duration_sec == 61


def test_timeline_huge_integer_rejected_cleanly_without_overflow_leak(reels_spec):
    """end_sec=10**10000 doğrudan dict inputta exception içine ham sayı sızdırmadan reddedilir."""
    data = _build_valid_content_dict(reels_spec)
    huge_int = 10**10000
    data["format_payload"]["segments"][-1]["end_sec"] = huge_int

    with pytest.raises(SocialContentOutputValidationError) as exc:
        validate_social_content_output(data, reels_spec)

    assert exc.value.error_code == CONTENT_CONTRACT_INVALID_DURATION
    err_str = str(exc.value)
    # Dev tamsayının string hali hata mesajında yer almamalıdır
    assert "1000000000" not in err_str
    assert len(err_str) < 500
