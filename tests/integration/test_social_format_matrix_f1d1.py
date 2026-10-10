# -*- coding: utf-8 -*-
"""Integration tests for Canonical Platform/Format Matrix and Read-only Endpoint (Phase F1-D.1).

Covers all 23 F1-D.1 requirements:
1. Endpoint 200 döner.
2. version tam olarak "v1"dir.
3. Limitler 1–5 keyword ve 1–6 target şeklindedir.
4. Yalnız beş canonical platform vardır.
5. Platform-format matrisi listeyle birebir aynıdır.
6. tiktok/carousel, instagram/thread ve youtube/carousel matrixte yoktur.
7. Twitter canonical ID "twitter", display label "X"tir.
8. Instagram story media_mode="static" ve requires_duration=false değerindedir.
9. reels, short ve video formatları süre gerektirir.
10. post, carousel, thread ve story süre gerektirmez.
11. Video formatlarının doğru duration profile bağlantıları vardır.
12. Video olmayan formatların duration profile/preset listesi boştur.
13. Her preset için min_sec >= 1 ve max_sec >= min_sec.
14. Aynı profil içindeki preset aralıkları çakışmaz.
15. Preset ID'leri global olarak tektir.
16. X video presetlerinin maksimumu 140 saniyedir.
17. Long video maksimumu 600 saniyedir.
18. Matrixte duplicate platform-format yoktur.
19. ContentFormatEnum.VIDEO.value == "video" olur.
20. Bilinmeyen platform/format/preset lookup'u fail-closed davranır (None döner).
21. Döndürülen bir matrix nesnesini değiştirme girişimi canonical sonraki çıktıyı değiştirmez.
22. /social/format-matrix isteği dinamik /social/{scoring_run_id} rotasına düşmez.
23. Endpoint çağrısı sırasında DB veya AI kullanımı olmadığını dependency override ile doğrula.
24. [F1-D.1a] Exact-match lookup: Uppercase ve whitespace içeren platform reddedilir (None).
25. [F1-D.1a] Exact-match lookup: Uppercase ve whitespace içeren format reddedilir (None).
26. [F1-D.1a] Exact-match lookup: Uppercase ve whitespace içeren preset ID reddedilir (None).
27. [F1-D.1a] Canonical mapping'ler immutable proxy'dir (DURATION_PROFILES item assignment TypeError üretir).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_ai, get_db
from app.generators.social.format_matrix import (
    CANONICAL_PLATFORMS,
    DURATION_PROFILES,
    FORMAT_MATRIX_VERSION,
    MAX_KEYWORDS,
    MAX_TARGETS,
    MIN_KEYWORDS,
    MIN_TARGETS,
    get_duration_preset,
    get_format_matrix,
    get_platform_format,
)
from app.main import app
from app.schemas.social import ContentFormatEnum
from app.schemas.social_brief import SocialFormatMatrixResponse


class TestSocialFormatMatrixF1D1:
    def test_01_endpoint_returns_200(self, client):
        """1. GET /api/v1/generation/social/format-matrix 200 döner."""
        resp = client.get("/api/v1/generation/social/format-matrix")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "limits" in data
        assert "platforms" in data

    def test_02_matrix_version_is_v1(self, client):
        """2. version tam olarak 'v1'dir."""
        matrix = get_format_matrix()
        assert matrix.version == "v1"
        assert FORMAT_MATRIX_VERSION == "v1"

        resp = client.get("/api/v1/generation/social/format-matrix")
        assert resp.json()["version"] == "v1"

    def test_03_limits_are_1_to_5_keywords_and_1_to_6_targets(self, client):
        """3. Limitler 1–5 keyword ve 1–6 target şeklindedir."""
        matrix = get_format_matrix()
        assert matrix.limits.min_keywords == 1
        assert matrix.limits.max_keywords == 5
        assert matrix.limits.min_targets == 1
        assert matrix.limits.max_targets == 6

        assert MIN_KEYWORDS == 1
        assert MAX_KEYWORDS == 5
        assert MIN_TARGETS == 1
        assert MAX_TARGETS == 6

    def test_04_only_five_canonical_platforms(self):
        """4. Yalnız beş canonical platform vardır."""
        matrix = get_format_matrix()
        platform_ids = [p.id for p in matrix.platforms]
        assert platform_ids == ["instagram", "tiktok", "twitter", "linkedin", "youtube"]
        assert len(matrix.platforms) == 5

    def test_05_platform_formats_match_canonical_spec(self):
        """5. Platform-format matrisi sözleşmeyle birebir aynıdır."""
        matrix = get_format_matrix()
        formats_by_platform = {
            p.id: [f.id for f in p.formats] for p in matrix.platforms
        }

        assert formats_by_platform == {
            "instagram": ["post", "carousel", "reels", "story"],
            "tiktok": ["short"],
            "twitter": ["post", "thread", "video"],
            "linkedin": ["post", "carousel", "video"],
            "youtube": ["short", "video"],
        }

    def test_06_non_existent_formats_not_in_matrix(self):
        """6. tiktok/carousel, instagram/thread ve youtube/carousel matrixte yoktur."""
        assert get_platform_format("tiktok", "carousel") is None
        assert get_platform_format("instagram", "thread") is None
        assert get_platform_format("youtube", "carousel") is None

        matrix = get_format_matrix()
        for p in matrix.platforms:
            fmt_ids = [f.id for f in p.formats]
            if p.id == "tiktok":
                assert "carousel" not in fmt_ids
            elif p.id == "instagram":
                assert "thread" not in fmt_ids
            elif p.id == "youtube":
                assert "carousel" not in fmt_ids

    def test_07_twitter_canonical_id_and_x_display_label(self):
        """7. Twitter canonical ID 'twitter', display label 'X'tir."""
        matrix = get_format_matrix()
        twitter_plat = next(p for p in matrix.platforms if p.id == "twitter")
        assert twitter_plat.id == "twitter"
        assert twitter_plat.label == "X"

    def test_08_instagram_story_is_static_and_no_duration(self):
        """8. Instagram story media_mode='static' ve requires_duration=false değerindedir."""
        fmt = get_platform_format("instagram", "story")
        assert fmt is not None
        assert fmt.id == "story"
        assert fmt.label == "Story (statik)"
        assert fmt.requires_duration is False
        assert fmt.media_mode == "static"
        assert fmt.duration_profile is None
        assert len(fmt.duration_presets) == 0

    def test_09_video_formats_require_duration(self):
        """9. reels, short ve video formatları süre gerektirir."""
        matrix = get_format_matrix()
        video_format_ids = {"reels", "short", "video"}
        for p in matrix.platforms:
            for f in p.formats:
                if f.id in video_format_ids:
                    assert f.requires_duration is True, f"{p.id}/{f.id} requires_duration True olmalıdır"

    def test_10_non_video_formats_do_not_require_duration(self):
        """10. post, carousel, thread ve story süre gerektirmez."""
        matrix = get_format_matrix()
        non_video_format_ids = {"post", "carousel", "thread", "story"}
        for p in matrix.platforms:
            for f in p.formats:
                if f.id in non_video_format_ids:
                    assert f.requires_duration is False, f"{p.id}/{f.id} requires_duration False olmalıdır"

    def test_11_video_formats_have_correct_duration_profiles(self):
        """11. Video formatlarının doğru duration profile bağlantıları vardır."""
        expected_profiles = {
            ("instagram", "reels"): "short_video",
            ("tiktok", "short"): "short_video",
            ("youtube", "short"): "short_video",
            ("twitter", "video"): "x_video",
            ("linkedin", "video"): "long_video",
            ("youtube", "video"): "long_video",
        }

        for (plat_id, fmt_id), expected_profile in expected_profiles.items():
            fmt = get_platform_format(plat_id, fmt_id)
            assert fmt is not None, f"{plat_id}/{fmt_id} bulunamadı"
            assert fmt.duration_profile == expected_profile, (
                f"{plat_id}/{fmt_id} profili {expected_profile} olmalı, alınan: {fmt.duration_profile}"
            )
            assert len(fmt.duration_presets) > 0, f"{plat_id}/{fmt_id} preset taşımalı"

    def test_12_non_video_formats_have_no_duration_profile_and_empty_presets(self):
        """12. Video olmayan formatların duration profile/preset listesi boştur."""
        matrix = get_format_matrix()
        for p in matrix.platforms:
            for f in p.formats:
                if not f.requires_duration:
                    assert f.duration_profile is None
                    assert f.duration_presets == []

    def test_13_duration_presets_min_max_bounds(self):
        """13. Her preset için min_sec >= 1 ve max_sec >= min_sec."""
        for profile_name, presets in DURATION_PROFILES.items():
            for p in presets:
                assert p.min_sec >= 1, f"{p.id} min_sec < 1"
                assert p.max_sec >= p.min_sec, f"{p.id} max_sec < min_sec"

    def test_14_duration_presets_intervals_do_not_overlap(self):
        """14. Aynı profil içindeki preset aralıkları çakışmaz."""
        for profile_name, presets in DURATION_PROFILES.items():
            sorted_p = sorted(presets, key=lambda x: x.min_sec)
            for i in range(len(sorted_p) - 1):
                assert sorted_p[i].max_sec < sorted_p[i + 1].min_sec, (
                    f"Profile '{profile_name}' içinde çakışma: "
                    f"[{sorted_p[i].min_sec}, {sorted_p[i].max_sec}] ve "
                    f"[{sorted_p[i + 1].min_sec}, {sorted_p[i + 1].max_sec}]"
                )

    def test_15_preset_ids_globally_unique(self):
        """15. Preset ID'leri global olarak tektir."""
        all_preset_ids = []
        for presets in DURATION_PROFILES.values():
            for p in presets:
                all_preset_ids.append(p.id)
        assert len(all_preset_ids) == len(set(all_preset_ids))

    def test_16_x_video_max_duration_140_seconds(self):
        """16. X video presetlerinin maksimumu 140 saniyedir."""
        x_presets = DURATION_PROFILES["x_video"]
        max_duration = max(p.max_sec for p in x_presets)
        assert max_duration == 140
        assert all(p.max_sec <= 140 for p in x_presets)

        x_preset_ids = [p.id for p in x_presets]
        assert x_preset_ids == ["x_1_15", "x_16_30", "x_31_60", "x_61_90", "x_91_140"]

    def test_17_long_video_max_duration_600_seconds(self):
        """17. Long video maksimumu 600 saniyedir."""
        long_presets = DURATION_PROFILES["long_video"]
        max_duration = max(p.max_sec for p in long_presets)
        assert max_duration == 600
        assert all(p.max_sec <= 600 for p in long_presets)

        long_preset_ids = [p.id for p in long_presets]
        assert long_preset_ids == ["long_60_180", "long_181_300", "long_301_600"]

    def test_18_no_duplicate_platform_format_pairs(self):
        """18. Matrixte duplicate platform-format yoktur."""
        seen = set()
        for p in CANONICAL_PLATFORMS:
            for f in p.formats:
                pair = (p.id, f.id)
                assert pair not in seen, f"Duplicate platform-format: {pair}"
                seen.add(pair)

    def test_19_content_format_enum_has_video(self):
        """19. ContentFormatEnum.VIDEO.value == 'video' olur."""
        assert ContentFormatEnum.VIDEO.value == "video"
        # Mevcut enum değerlerinin korunduğunu doğrula
        assert ContentFormatEnum.REELS.value == "reels"
        assert ContentFormatEnum.CAROUSEL.value == "carousel"
        assert ContentFormatEnum.STORY.value == "story"
        assert ContentFormatEnum.POST.value == "post"
        assert ContentFormatEnum.THREAD.value == "thread"
        assert ContentFormatEnum.SHORT.value == "short"

    def test_20_lookup_helpers_fail_closed(self):
        """20. Bilinmeyen platform/format/preset lookup'u fail-closed davranır (None döner)."""
        # Bilinmeyen platform
        assert get_platform_format("unknown_platform", "post") is None
        # Bilinmeyen format
        assert get_platform_format("instagram", "unknown_format") is None
        # Desteklenmeyen eşleşme
        assert get_platform_format("tiktok", "carousel") is None
        # Geçersiz tip
        assert get_platform_format(None, "post") is None  # type: ignore
        assert get_platform_format("instagram", 123) is None  # type: ignore

        # Bilinmeyen preset
        assert get_duration_preset("unknown_preset") is None
        assert get_duration_preset(None) is None  # type: ignore

        # Geçerli sorgular
        valid_fmt = get_platform_format("twitter", "video")
        assert valid_fmt is not None
        assert valid_fmt.duration_profile == "x_video"

        valid_preset = get_duration_preset("short_1_15")
        assert valid_preset is not None
        assert valid_preset.min_sec == 1 and valid_preset.max_sec == 15

    def test_21_returned_matrix_mutation_does_not_affect_canonical(self):
        """21. Döndürülen bir matrix nesnesini değiştirme girişimi canonical sonraki çıktıyı değiştirmez."""
        matrix1 = get_format_matrix()
        # Dış katman döndürülen nesneyi mutasyona uğratıyor
        matrix1.platforms.clear()
        assert len(matrix1.platforms) == 0

        # Sonraki çağrı temiz canonical kopyayı döndürmelidir
        matrix2 = get_format_matrix()
        assert len(matrix2.platforms) == 5
        assert matrix2.platforms[0].id == "instagram"

    def test_22_endpoint_does_not_conflict_with_dynamic_scoring_run_id_route(self, client):
        """22. /social/format-matrix isteği dinamik /social/{scoring_run_id} rotasına düşmez."""
        # Eğer dinamik rotaya düşseydi scoring_run_id='format-matrix' (int değil)
        # veya brand_profile_id zorunlu Query parametresi eksikliğinden 422 dönerdi.
        resp = client.get("/api/v1/generation/social/format-matrix")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "v1"
        assert "platforms" in data

    def test_23_endpoint_calls_no_db_and_no_ai(self, client):
        """23. Endpoint çağrısı sırasında DB veya AI kullanımı olmadığını dependency override ile doğrula."""
        def fail_if_db_called():
            raise AssertionError("Endpoint DB dependency (get_db) ÇAĞIRMAMALIDIR!")

        def fail_if_ai_called():
            raise AssertionError("Endpoint AI dependency (get_ai) ÇAĞIRMAMALIDIR!")

        orig_db = app.dependency_overrides.get(get_db)
        orig_ai = app.dependency_overrides.get(get_ai)
        app.dependency_overrides[get_db] = fail_if_db_called
        app.dependency_overrides[get_ai] = fail_if_ai_called
        try:
            resp = client.get("/api/v1/generation/social/format-matrix")
            assert resp.status_code == 200
            assert resp.json()["version"] == "v1"
        finally:
            if orig_db is not None:
                app.dependency_overrides[get_db] = orig_db
            else:
                app.dependency_overrides.pop(get_db, None)
            if orig_ai is not None:
                app.dependency_overrides[get_ai] = orig_ai
            else:
                app.dependency_overrides.pop(get_ai, None)

    def test_24_exact_match_rejects_casing_and_whitespace_platform(self):
        """24. [F1-D.1a] Uppercase veya baştaki/sondaki whitespace bulunan platform reddedilir (None)."""
        # Casing
        assert get_platform_format("Instagram", "post") is None
        assert get_platform_format("INSTAGRAM", "post") is None
        assert get_platform_format("Twitter", "video") is None
        assert get_platform_format("TWITTER", "video") is None
        assert get_platform_format("TikTok", "short") is None

        # Whitespace
        assert get_platform_format(" instagram", "post") is None
        assert get_platform_format("instagram ", "post") is None
        assert get_platform_format(" instagram ", "post") is None
        assert get_platform_format("\tinstagram", "post") is None
        assert get_platform_format("twitter ", "video") is None

        # Exact match geçerli
        assert get_platform_format("instagram", "post") is not None
        assert get_platform_format("twitter", "video") is not None

    def test_25_exact_match_rejects_casing_and_whitespace_format(self):
        """25. [F1-D.1a] Uppercase veya baştaki/sondaki whitespace bulunan format reddedilir (None)."""
        # Casing
        assert get_platform_format("instagram", "POST") is None
        assert get_platform_format("instagram", "Post") is None
        assert get_platform_format("instagram", "Reels") is None
        assert get_platform_format("twitter", "VIDEO") is None
        assert get_platform_format("tiktok", "SHORT") is None

        # Whitespace
        assert get_platform_format("instagram", " post") is None
        assert get_platform_format("instagram", "post ") is None
        assert get_platform_format("instagram", " post ") is None
        assert get_platform_format("twitter", "\tvideo") is None

        # Exact match geçerli
        assert get_platform_format("instagram", "reels") is not None
        assert get_platform_format("tiktok", "short") is not None

    def test_26_exact_match_rejects_casing_and_whitespace_preset_id(self):
        """26. [F1-D.1a] Uppercase veya baştaki/sondaki whitespace bulunan preset ID reddedilir (None)."""
        # Casing
        assert get_duration_preset("SHORT_1_15") is None
        assert get_duration_preset("Short_1_15") is None
        assert get_duration_preset("X_91_140") is None
        assert get_duration_preset("LONG_60_180") is None

        # Whitespace
        assert get_duration_preset(" short_1_15") is None
        assert get_duration_preset("short_1_15 ") is None
        assert get_duration_preset(" short_1_15 ") is None
        assert get_duration_preset("\tx_91_140") is None

        # Exact match geçerli
        assert get_duration_preset("short_1_15") is not None
        assert get_duration_preset("x_91_140") is not None
        assert get_duration_preset("long_60_180") is not None

    def test_27_canonical_mapping_immutable_proxy(self):
        """27. [F1-D.1a] DURATION_PROFILES immutable mappingproxy'dir; mutasyon TypeError fırlatır."""
        from types import MappingProxyType

        assert isinstance(DURATION_PROFILES, MappingProxyType)

        # Yeni anahtar ekleme girişimi
        with pytest.raises(TypeError):
            DURATION_PROFILES["evil"] = ()  # type: ignore

        # Mevcut anahtarı değiştirme girişimi
        with pytest.raises(TypeError):
            DURATION_PROFILES["short_video"] = ()  # type: ignore

        # Anahtar silme girişimi
        with pytest.raises(TypeError):
            del DURATION_PROFILES["short_video"]  # type: ignore
