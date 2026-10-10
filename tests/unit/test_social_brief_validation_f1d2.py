# -*- coding: utf-8 -*-
"""Unit tests for SocialBrief domain validation and request schemas (F1-D.2).

Tüm testler saf (pure) domain mantığını test eder; DB, ORM, oturum veya HTTP bağımlılığı yoktur.
"""
from __future__ import annotations

import dataclasses
import inspect
from typing import Any, List

import pytest
from pydantic import ValidationError

import app.core.social.brief_validation as bv
from app.core.social.brief_validation import (
    SocialBriefValidationError,
    ValidatedSocialBriefInput,
    ValidatedSocialBriefTarget,
    validate_social_brief_request,
)
from app.schemas.social_brief import (
    SocialBriefCreateRequest,
    SocialBriefTargetCreateRequest,
)


# ==================== 1. ŞEMA TESTLERİ (1-7) ====================

def test_01_valid_request_parsed():
    """1. Geçerli request parse edilir ve doğrulanır."""
    req = SocialBriefCreateRequest(
        scoring_run_id=42,
        keyword_ids=[10, 20, 30],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(
                platform="instagram",
                content_format="reels",
                duration_preset_id="short_16_30",
            ),
        ],
        brand_name="Test Marka",
        brand_context="Test bağlam notları",
    )
    res = validate_social_brief_request(req)
    assert isinstance(res, ValidatedSocialBriefInput)
    assert res.scoring_run_id == 42
    assert res.keyword_ids == (10, 20, 30)
    assert len(res.targets) == 2
    assert res.brand_name == "Test Marka"
    assert res.brand_context == "Test bağlam notları"
    assert res.format_matrix_version == "v1"


def test_02_scoring_run_id_string_rejected():
    """2. scoring_run_id string verilirse pydantic tarafından reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefCreateRequest(
            scoring_run_id="42",  # type: ignore[arg-type]
            keyword_ids=[1],
            targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
        )


def test_03_keyword_id_string_rejected():
    """3. keyword ID string verilirse pydantic tarafından reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefCreateRequest(
            scoring_run_id=1,
            keyword_ids=[1, "2"],  # type: ignore[list-item]
            targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
        )


def test_04_bool_keyword_or_scoring_run_id_rejected():
    """4. bool keyword veya scoring_run_id pydantic tarafından reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefCreateRequest(
            scoring_run_id=True,  # type: ignore[arg-type]
            keyword_ids=[1],
            targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
        )

    with pytest.raises(ValidationError):
        SocialBriefCreateRequest(
            scoring_run_id=1,
            keyword_ids=[True],  # type: ignore[list-item]
            targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
        )


def test_05_unknown_request_field_rejected():
    """5. Bilinmeyen request alanı extra='forbid' ile reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefCreateRequest(
            scoring_run_id=1,
            keyword_ids=[1],
            targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
            malicious_field="unexpected_value",  # type: ignore[call-arg]
        )


def test_06_target_duration_injection_rejected():
    """6. Target içine duration_min_sec/max_sec enjekte edilirse extra='forbid' ile reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefTargetCreateRequest(
            platform="instagram",
            content_format="reels",
            duration_preset_id="short_1_15",
            duration_min_sec=1,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        SocialBriefTargetCreateRequest(
            platform="instagram",
            content_format="reels",
            duration_preset_id="short_1_15",
            duration_max_sec=15,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        SocialBriefTargetCreateRequest(
            platform="instagram",
            content_format="reels",
            duration_preset_id="short_1_15",
            duration_profile="short_video",  # type: ignore[call-arg]
        )


def test_07_platform_format_strings_not_coerced():
    """7. Platform/format stringleri trim veya lowercase edilmez; domain doğrulayıcısı reddeder."""
    t_upper = SocialBriefTargetCreateRequest(platform="Instagram", content_format="post")
    assert t_upper.platform == "Instagram"  # Pydantic trim/lowercase yapmaz

    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[t_upper],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "UNSUPPORTED_PLATFORM_FORMAT"


# ==================== 2. KEYWORD DOMAIN TESTLERİ (8-13) ====================

def test_08_zero_keywords_rejected():
    """8. 0 keyword reddedilir (INVALID_KEYWORD_COUNT)."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "INVALID_KEYWORD_COUNT"
    assert exc.value.field == "keyword_ids"


def test_09_six_keywords_rejected():
    """9. 6 keyword reddedilir (INVALID_KEYWORD_COUNT)."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1, 2, 3, 4, 5, 6],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "INVALID_KEYWORD_COUNT"
    assert exc.value.field == "keyword_ids"


def test_10_one_and_five_keywords_accepted():
    """10. Sınır değerler olan 1 ve 5 keyword kabul edilir."""
    req_1 = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[101],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    res_1 = validate_social_brief_request(req_1)
    assert len(res_1.keyword_ids) == 1

    req_5 = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[101, 102, 103, 104, 105],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    res_5 = validate_social_brief_request(req_5)
    assert len(res_5.keyword_ids) == 5


def test_11_duplicate_keyword_rejected():
    """11. Duplicate keyword ID reddedilir (DUPLICATE_KEYWORD)."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[101, 102, 101],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "DUPLICATE_KEYWORD"
    assert exc.value.field == "keyword_ids"


def test_12_invalid_keyword_id_in_direct_call_rejected():
    """12. Sıfır/negatif/bool/non-int ID doğrudan domain çağrısında fail-closed reddedilir."""
    # model_construct ile Pydantic bypass edilse dahi domain doğrulayıcısı yakalar
    req_zero = SocialBriefCreateRequest.model_construct(
        scoring_run_id=1,
        keyword_ids=[0],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_zero)
    assert exc.value.error_code == "INVALID_KEYWORD_ID"
    assert exc.value.field == "keyword_ids"

    req_neg = SocialBriefCreateRequest.model_construct(
        scoring_run_id=1,
        keyword_ids=[-5],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_neg)
    assert exc.value.error_code == "INVALID_KEYWORD_ID"

    req_bool = SocialBriefCreateRequest.model_construct(
        scoring_run_id=1,
        keyword_ids=[True],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_bool)
    assert exc.value.error_code == "INVALID_KEYWORD_ID"

    req_str = SocialBriefCreateRequest.model_construct(
        scoring_run_id=1,
        keyword_ids=["not_an_int"],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_str)
    assert exc.value.error_code == "INVALID_KEYWORD_ID"


def test_13_keyword_order_preserved():
    """13. Kullanıcının verdiği keyword sırası mutasyonsuz ve sıralanmadan korunur."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[55, 12, 99, 3],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    res = validate_social_brief_request(req)
    assert res.keyword_ids == (55, 12, 99, 3)


# ==================== 3. TARGET DOMAIN TESTLERİ (14-22) ====================

def test_14_zero_targets_rejected():
    """14. 0 target reddedilir (INVALID_TARGET_COUNT)."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "INVALID_TARGET_COUNT"
    assert exc.value.field == "targets"


def test_15_seven_targets_rejected():
    """15. 7 target reddedilir (INVALID_TARGET_COUNT)."""
    targets = [
        SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="carousel"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="post"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
        SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
        SocialBriefTargetCreateRequest(platform="linkedin", content_format="carousel"),
    ]
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=targets,
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "INVALID_TARGET_COUNT"
    assert exc.value.field == "targets"


def test_16_one_and_six_targets_accepted():
    """16. Sınır değerler olan 1 ve 6 target kabul edilir."""
    req_1 = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    res_1 = validate_social_brief_request(req_1)
    assert len(res_1.targets) == 1

    targets_6 = [
        SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="carousel"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="post"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
        SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
    ]
    req_6 = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=targets_6,
    )
    res_6 = validate_social_brief_request(req_6)
    assert len(res_6.targets) == 6


def test_17_duplicate_target_rejected():
    """17. Aynı platform ve content_format çifti iki kez verilirse DUPLICATE_TARGET üretir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "DUPLICATE_TARGET"
    assert exc.value.field == "targets[1]"


def test_18_same_platform_different_formats_accepted():
    """18. Aynı platform altında farklı formatlar geçerlidir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="reels", duration_preset_id="short_16_30"
            ),
            SocialBriefTargetCreateRequest(platform="instagram", content_format="carousel"),
        ],
    )
    res = validate_social_brief_request(req)
    assert len(res.targets) == 3


def test_19_different_platforms_same_format_accepted():
    """19. Farklı platformlardaki aynı format ismi geçerlidir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(platform="twitter", content_format="post"),
            SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
        ],
    )
    res = validate_social_brief_request(req)
    assert len(res.targets) == 3


def test_20_unsupported_platform_format_rejected():
    """20. Desteklenmeyen platform-format çifti UNSUPPORTED_PLATFORM_FORMAT üretir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="tiktok", content_format="carousel"),
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "UNSUPPORTED_PLATFORM_FORMAT"
    assert exc.value.field == "targets[0]"


def test_21_uppercase_and_whitespace_platform_format_rejected():
    """21. Uppercase veya whitespace içeren platform/format tam eşleşmediği için reddedilir."""
    # Case mismatch
    req_upper = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="POST")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_upper)
    assert exc.value.error_code == "UNSUPPORTED_PLATFORM_FORMAT"

    # Whitespace in platform
    req_space = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[SocialBriefTargetCreateRequest(platform=" instagram", content_format="post")],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_space)
    assert exc.value.error_code == "UNSUPPORTED_PLATFORM_FORMAT"


def test_22_target_order_preserved():
    """22. Hedeflerin kullanıcı tarafından verilen sırası korunur."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
            SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
            SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
        ],
    )
    res = validate_social_brief_request(req)
    assert res.targets[0].platform == "linkedin"
    assert res.targets[0].content_format == "post"
    assert res.targets[1].platform == "instagram"
    assert res.targets[1].content_format == "story"
    assert res.targets[2].platform == "twitter"
    assert res.targets[2].content_format == "thread"


# ==================== 4. DURATION TESTLERİ (23-31) ====================

def test_23_valid_video_target_resolves_min_max():
    """23. Her geçerli video eşleşmesi canonical min_sec ve max_sec üretir."""
    # Instagram Reels
    req_reels = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="reels", duration_preset_id="short_16_30"
            )
        ],
    )
    res_reels = validate_social_brief_request(req_reels)
    assert res_reels.targets[0].duration_preset_id == "short_16_30"
    assert res_reels.targets[0].duration_min_sec == 16
    assert res_reels.targets[0].duration_max_sec == 30

    # TikTok Short
    req_tt = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="tiktok", content_format="short", duration_preset_id="short_1_15"
            )
        ],
    )
    res_tt = validate_social_brief_request(req_tt)
    assert res_tt.targets[0].duration_preset_id == "short_1_15"
    assert res_tt.targets[0].duration_min_sec == 1
    assert res_tt.targets[0].duration_max_sec == 15

    # LinkedIn Video
    req_li = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="linkedin", content_format="video", duration_preset_id="long_181_300"
            )
        ],
    )
    res_li = validate_social_brief_request(req_li)
    assert res_li.targets[0].duration_preset_id == "long_181_300"
    assert res_li.targets[0].duration_min_sec == 181
    assert res_li.targets[0].duration_max_sec == 300


def test_24_video_without_preset_rejected():
    """24. Video formatı preset olmadan verilirse DURATION_PRESET_REQUIRED üretir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="reels", duration_preset_id=None)
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "DURATION_PRESET_REQUIRED"
    assert exc.value.field == "targets[0].duration_preset_id"


def test_25_unknown_preset_rejected():
    """25. Bilinmeyen preset ID INVALID_DURATION_PRESET üretir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="reels", duration_preset_id="unknown_preset_123"
            )
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "INVALID_DURATION_PRESET"
    assert exc.value.field == "targets[0].duration_preset_id"


def test_26_mismatched_preset_profile_rejected():
    """26. Başka profile ait geçerli preset verilirse DURATION_PROFILE_MISMATCH üretir."""
    # Instagram Reels (short_video) + long_60_180 (long_video)
    req_reels_long = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="reels", duration_preset_id="long_60_180"
            )
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_reels_long)
    assert exc.value.error_code == "DURATION_PROFILE_MISMATCH"
    assert exc.value.field == "targets[0].duration_preset_id"

    # Twitter Video (x_video) + short_61_90 (short_video)
    req_tw_short = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="twitter", content_format="video", duration_preset_id="short_61_90"
            )
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_tw_short)
    assert exc.value.error_code == "DURATION_PROFILE_MISMATCH"
    assert exc.value.field == "targets[0].duration_preset_id"


def test_27_non_video_preset_rejected():
    """27. Non-video formatta duration_preset verilirse DURATION_NOT_ALLOWED üretir."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="post", duration_preset_id="short_1_15"
            )
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req)
    assert exc.value.error_code == "DURATION_NOT_ALLOWED"
    assert exc.value.field == "targets[0].duration_preset_id"


def test_28_empty_string_preset_rejected():
    """28. Boş string preset None sayılmaz; video için INVALID_DURATION_PRESET, non-video için DURATION_NOT_ALLOWED üretir."""
    # Video format
    req_video = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="reels", duration_preset_id="")
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_video)
    assert exc.value.error_code == "INVALID_DURATION_PRESET"

    # Non-video format
    req_non_video = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post", duration_preset_id="")
        ],
    )
    with pytest.raises(SocialBriefValidationError) as exc:
        validate_social_brief_request(req_non_video)
    assert exc.value.error_code == "DURATION_NOT_ALLOWED"


def test_29_x_video_91_140_resolved():
    """29. X video 91–140 doğru çözülür."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="twitter", content_format="video", duration_preset_id="x_91_140"
            )
        ],
    )
    res = validate_social_brief_request(req)
    assert res.targets[0].duration_preset_id == "x_91_140"
    assert res.targets[0].duration_min_sec == 91
    assert res.targets[0].duration_max_sec == 140


def test_30_long_video_301_600_resolved():
    """30. Long video 301–600 doğru çözülür."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="youtube", content_format="video", duration_preset_id="long_301_600"
            )
        ],
    )
    res = validate_social_brief_request(req)
    assert res.targets[0].duration_preset_id == "long_301_600"
    assert res.targets[0].duration_min_sec == 301
    assert res.targets[0].duration_max_sec == 600


def test_31_non_video_targets_have_none_durations():
    """31. Video olmayan sonuçlarda üç duration alanı da None olur."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(platform="instagram", content_format="carousel"),
            SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
            SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
        ],
    )
    res = validate_social_brief_request(req)
    for target in res.targets:
        assert target.duration_preset_id is None
        assert target.duration_min_sec is None
        assert target.duration_max_sec is None


# ==================== 5. SONUÇ VE HATA TESTLERİ (32-36) ====================

def test_32_result_dataclasses_are_immutable():
    """32. Sonuç dataclass'ları immutable'dır (mutasyon denemesi hata verir)."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[
            SocialBriefTargetCreateRequest(
                platform="instagram", content_format="reels", duration_preset_id="short_1_15"
            )
        ],
    )
    res = validate_social_brief_request(req)

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.scoring_run_id = 999  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.targets[0].platform = "tiktok"  # type: ignore[misc]


def test_33_format_matrix_version_is_v1():
    """33. format_matrix_version 'v1' olur."""
    req = SocialBriefCreateRequest(
        scoring_run_id=1,
        keyword_ids=[1],
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )
    res = validate_social_brief_request(req)
    assert res.format_matrix_version == "v1"


def test_34_input_request_not_mutated():
    """34. Doğrulama sırasında input request nesnesi asla mutate edilmez."""
    original_keywords = [10, 20, 30]
    t1 = SocialBriefTargetCreateRequest(platform="instagram", content_format="post")
    t2 = SocialBriefTargetCreateRequest(
        platform="instagram", content_format="reels", duration_preset_id="short_1_15"
    )
    original_targets = [t1, t2]

    req = SocialBriefCreateRequest(
        scoring_run_id=100,
        keyword_ids=list(original_keywords),
        targets=list(original_targets),
        brand_name="Original",
        brand_context="Original Context",
    )

    res = validate_social_brief_request(req)
    assert req.keyword_ids == [10, 20, 30]
    assert req.targets == [t1, t2]
    assert req.brand_name == "Original"
    assert req.brand_context == "Original Context"


def test_35_all_errors_have_stable_error_code_and_field():
    """35. Her domain hatası kararlı error_code ve anlamlı field taşır."""
    expected_codes = {
        "INVALID_KEYWORD_COUNT",
        "INVALID_KEYWORD_ID",
        "DUPLICATE_KEYWORD",
        "INVALID_TARGET_COUNT",
        "INVALID_TARGET_VALUE",
        "DUPLICATE_TARGET",
        "UNSUPPORTED_PLATFORM_FORMAT",
        "DURATION_PRESET_REQUIRED",
        "DURATION_NOT_ALLOWED",
        "INVALID_DURATION_PRESET",
        "DURATION_PROFILE_MISMATCH",
    }

    # Her senaryoyu tetikleyip kod ve field'ı test edelim
    cases = [
        # (request, expected_code, expected_field_prefix)
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]),
            "INVALID_KEYWORD_COUNT", "keyword_ids"
        ),
        (
            SocialBriefCreateRequest.model_construct(scoring_run_id=1, keyword_ids=[-1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]),
            "INVALID_KEYWORD_ID", "keyword_ids"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1, 1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]),
            "DUPLICATE_KEYWORD", "keyword_ids"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[]),
            "INVALID_TARGET_COUNT", "targets"
        ),
        (
            SocialBriefCreateRequest.model_construct(scoring_run_id=-1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]),
            "INVALID_TARGET_VALUE", "scoring_run_id"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post"), SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]),
            "DUPLICATE_TARGET", "targets[1]"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="tiktok", content_format="post")]),
            "UNSUPPORTED_PLATFORM_FORMAT", "targets[0]"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="reels")]),
            "DURATION_PRESET_REQUIRED", "targets[0].duration_preset_id"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post", duration_preset_id="short_1_15")]),
            "DURATION_NOT_ALLOWED", "targets[0].duration_preset_id"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="reels", duration_preset_id="invalid")]),
            "INVALID_DURATION_PRESET", "targets[0].duration_preset_id"
        ),
        (
            SocialBriefCreateRequest(scoring_run_id=1, keyword_ids=[1], targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="reels", duration_preset_id="long_60_180")]),
            "DURATION_PROFILE_MISMATCH", "targets[0].duration_preset_id"
        ),
    ]

    seen_test_codes = set()
    for req, exp_code, exp_field in cases:
        with pytest.raises(SocialBriefValidationError) as exc:
            validate_social_brief_request(req)
        assert exc.value.error_code == exp_code
        assert exc.value.field == exp_field
        seen_test_codes.add(exc.value.error_code)

    # 11 kodun 11'inin de bu senaryolarla test edildiğini doğrula
    assert seen_test_codes == expected_codes


def test_36_no_db_or_http_dependencies():
    """36. Modülün SQLAlchemy, Session, FastAPI veya HTTPException bağımlılığı olmadığını doğrula."""
    source = inspect.getsource(bv)
    assert "sqlalchemy" not in source, "brief_validation module must not import or reference sqlalchemy"
    assert "fastapi" not in source, "brief_validation module must not import or reference fastapi"
    assert "HTTPException" not in source, "brief_validation module must not import or reference HTTPException"
    assert "Session" not in source, "brief_validation module must not reference DB Session"
