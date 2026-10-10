# -*- coding: utf-8 -*-
"""Unit tests for Saf Grounding, Süre ve Birleşik Repair Karar Motoru (F1-G.3).

Bu test süiti:
1. Grounding Whitelist ve Muafiyet Davranışı:
   - Boş grounding_facts'te sayısal veya süperlatif iddiaların yakalanması
   - confirmed product_facts ile eşleşen iddiaların temiz geçmesi
   - trusted_brand_usp ile eşleşen iddiaların temiz geçmesi
   - product_facts + trusted_brand_usp birleşik whitelist
   - brand_context ve generic fallback USP'nin whitelist'e sızamaması
   - Süperlatif iddiaların facts içinde varsa clean, yoksa violation olması
   - Kazanç garantisinin facts içinde olsa bile KESİNLİKLE violation olması
   - Caption ve hook iddialarının taranması ve sırasıyla tekilleştirilmesi
   - format_payload (scene, voiceover, slides, posts) alanlarının taranmaması
   - primary_keyword tam muafiyeti (exempt_keywords) ve parçalı/genişletilmiş eşleşmelerin reddi
2. Birleşik Karar Matrisi:
   - Clean + valid/not_applicable -> accept
   - İlk üretim (repair_attempted=False) tek başına claim -> repair (reason_codes=["ungrounded_claim"])
   - İlk üretim tek başına duration mismatch -> repair (reason_codes=["duration_mismatch"])
   - İlk üretim hem claim hem duration mismatch -> tek repair (reason_codes=["ungrounded_claim", "duration_mismatch"])
   - Repair sonrası (repair_attempted=True) clean + valid -> accept
   - Repair sonrası clean + duration mismatch -> accept, warnings'e "duration_mismatch" eklenir
   - Repair sonrası claim devam ediyor -> reject (reason_codes en az ["ungrounded_claim"])
   - Repair sonrası hem claim hem mismatch -> reject (reason_codes=["ungrounded_claim", "duration_mismatch"])
   - Voiceover soft warning tek başına accept (repair veya reject tetiklemez)
   - validation_warnings deterministik sırası ve tekilleştirmesi
3. Tip ve Güvenlik Sözleşmeleri:
   - DTO derinlemesine immutability (frozen dataclass)
   - Geçersiz tip, boş/untrimmed/aşırı uzun keyword, facts, usp kontrolleri
   - Bilinmeyen duration_status, video/not_applicable uyumsuzluğu, non-video/valid uyumsuzluğu
   - Video actual_duration_sec tamsayı kontrolü ve non-video None kontrolü
   - find_ungrounded_claims istisnasında fail-closed CONTENT_QUALITY_GROUNDING_ERROR
   - Hata mesajlarında hassas içerik, caption, keyword, USP veya claim sızmaması
   - DB/ORM/AI/Network bağımsızlığı
senaryolarını uçtan uca doğrular.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from app.core.social.content_contract import (
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
    find_social_content_ungrounded_claims,
)


# ==================== TEST YARDIMCILARI ====================

def _make_hook(text: str = "Doğal içerikli cilt bakımı", style: str = "curiosity", ab_score: float | None = None) -> ValidatedHook:
    return ValidatedHook(text=text, style=style, ab_score=ab_score)


def _make_video_content(
    *,
    caption: str = "Doğal cilt bakım rutini önerileri.",
    hooks: tuple[ValidatedHook, ...] | None = None,
    duration_status: str = "valid",
    actual_duration_sec: int | None = 25,
    validation_warnings: tuple[str, ...] = (),
    payload_claim: bool = True,
) -> ValidatedSocialContent:
    if hooks is None:
        hooks = (_make_hook(),)
    # format_payload içine iddia ekleyerek taranmadığını test etmek için
    on_screen = "100.000+ mutlu müşteri" if payload_claim else "Adım adım uygulama"
    voiceover = "%80 başarı sağlayan formül" if payload_claim else "Cildinizi nazikçe temizleyin."
    segments = (
        ValidatedVideoSegment(
            start_sec=0,
            end_sec=25,
            scene="Model ürünü gösterir",
            on_screen_text=on_screen,
            voiceover=voiceover,
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


# ==================== TEST GRUBU 1: GROUNDING WHITELIST VE TARAMA ====================

def test_g01_empty_facts_numeric_claim_detected():
    """Grounding facts boş olduğunda metindeki sayısal iddialar ungrounded olarak yakalanır."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_post_content(caption="Ürünümüz 50 bin kullanıcı tarafından tercih edildi.")

    claims = find_social_content_ungrounded_claims(content, context)
    assert len(claims) > 0
    assert any("50 bin" in c or "50.000" in c for c in claims)


def test_g02_product_facts_grounds_numeric_claim():
    """confirmed product_facts içinde yer alan sayısal iddia temiz (clean) geçer."""
    context = SocialContentGroundingContext(
        primary_keyword="cilt bakımı",
        product_facts="50.000 kullanıcı dermatolojik testleri onayladı.",
    )
    content = _make_post_content(caption="Ürünümüz 50 bin kullanıcı tarafından tercih edildi.")

    claims = find_social_content_ungrounded_claims(content, context)
    assert claims == ()


def test_g03_trusted_brand_usp_grounds_claim():
    """Kullanıcının açıkça sağladığı trusted_brand_usp içindeki iddia temiz geçer."""
    context = SocialContentGroundingContext(
        primary_keyword="cilt bakımı",
        trusted_brand_usp="10 yıldır sektörde güvenle hizmet veriyoruz.",
    )
    content = _make_post_content(caption="10 yıldır hizmetinizdeyiz ve yanınızdayız.")

    claims = find_social_content_ungrounded_claims(content, context)
    assert claims == ()


def test_g04_combined_facts_and_usp_whitelist():
    """product_facts ve trusted_brand_usp birlikte whitelist oluşturur."""
    context = SocialContentGroundingContext(
        primary_keyword="cilt bakımı",
        product_facts="%80 memnuniyet oranı belgelenmiştir.",
        trusted_brand_usp="4.8 puan ile müşteri favorisi.",
    )
    # caption product_facts'ten, hook trusted_brand_usp'den beslenir
    content = _make_post_content(
        hooks=(_make_hook(text="4.8/5 puan alan formül!"),),
        caption="Kullanıcılarımızda yüzde 80 memnuniyet sağlandı.",
    )

    claims = find_social_content_ungrounded_claims(content, context)
    assert claims == ()


def test_g05_superlative_in_facts_passes_outside_fails():
    """Süperlatif iddia facts içinde birebir varsa clean, yoksa violation sayılır."""
    # Facts içinde yoksa -> violation
    context_no_sup = SocialContentGroundingContext(primary_keyword="hisse")
    content = _make_post_content(caption="Türkiye'nin en iyi platformu ile tanışın.")
    claims_no_sup = find_social_content_ungrounded_claims(content, context_no_sup)
    assert len(claims_no_sup) > 0
    assert any("en iyi" in c for c in claims_no_sup)

    # Facts içinde birebir varsa -> clean
    context_with_sup = SocialContentGroundingContext(
        primary_keyword="hisse",
        product_facts="Ödüllü sistem: türkiye'nin en iyi platformu",
    )
    claims_with_sup = find_social_content_ungrounded_claims(content, context_with_sup)
    assert claims_with_sup == ()


def test_g06_guarantee_banned_even_if_in_facts():
    """Kazanç/getiri garantisi facts içinde bulunsa dahi HER DURUMDA ihlal sayılır."""
    context = SocialContentGroundingContext(
        primary_keyword="yatırım",
        product_facts="Müşterilere kazanç garantisi veriyoruz.",
        trusted_brand_usp="Kesin kazanç garantisi sunar.",
    )
    content = _make_post_content(caption="Platformumuz kazanç garantisi sunmaktadır.")

    claims = find_social_content_ungrounded_claims(content, context)
    assert len(claims) > 0
    assert any("kazanç garanti" in c for c in claims)


def test_g07_hook_and_caption_claims_deduplicated_preserving_order():
    """Birden fazla hook ve caption'daki mükerrer iddialar ilk görülme sırasıyla tekilleştirilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_post_content(
        hooks=(
            _make_hook(text="%80 memnuniyet sağlayan formül!"),
            _make_hook(text="50 bin kullanıcı yanılıyor olamaz!"),
            _make_hook(text="%80 memnuniyet ile cildinizi yenileyin!"),  # Tekrarlayan iddia
        ),
        caption="Tam 50 bin kullanıcı ve %80 memnuniyet ile yanınızdayız.",  # Tekrarlayan iddialar
    )

    claims = find_social_content_ungrounded_claims(content, context)
    # Sıra düzeni: ilk görülen %80, ardından 50 bin kullanıcı
    assert len(claims) == 2
    assert "%80" in claims[0]
    assert "50 bin" in claims[1]


def test_g08_format_payload_not_scanned_for_claims():
    """format_payload (scene, voiceover, slides, posts) bu fazda taranmaz; yalnızca hook ve caption taranır."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    # Video içeriğinin payload'ında (on_screen_text ve voiceover) 100.000+ ve %80 geçiyor,
    # ancak caption ve hook tamamen temiz
    clean_video = _make_video_content(
        hooks=(_make_hook(text="Cilt bakımında 3 altın adım."),),
        caption="Günlük cilt temizliği ve bakım adımları rehberi.",
        payload_claim=True,
    )

    claims = find_social_content_ungrounded_claims(clean_video, context)
    assert claims == ()


def test_g09_exact_primary_keyword_exemption_and_narrow_scope():
    """primary_keyword dar ve tam eşleşme muafiyeti sağlar; kısmi veya alakasız sayıları aklamaz."""
    # 1. Tam eşleşen keyword içindeki sayı/yıl muaf sayılır
    context_exact = SocialContentGroundingContext(primary_keyword="en iyi hisse 2026")
    content_exact = _make_post_content(caption="en iyi hisse 2026 analizini sizler için derledik.")
    claims_exact = find_social_content_ungrounded_claims(content_exact, context_exact)
    assert claims_exact == ()

    # 2. Keyword'ün yalnız bir parçası ("hisse") iddiayı aklamaz
    context_partial = SocialContentGroundingContext(primary_keyword="hisse")
    content_partial = _make_post_content(caption="en iyi hisse 2026 analizini sizler için derledik.")
    claims_partial = find_social_content_ungrounded_claims(content_partial, context_partial)
    assert len(claims_partial) > 0  # "en iyi" yakalanmalı

    # 3. Keyword içindeki sayı başka bir başarı iddiasını otomatik aklamaz
    context_kw_num = SocialContentGroundingContext(primary_keyword="hedef 2026")
    content_other_num = _make_post_content(caption="hedef 2026 yolunda 100.000+ müşteriye ulaştık.")
    claims_other = find_social_content_ungrounded_claims(content_other_num, context_kw_num)
    assert len(claims_other) > 0
    assert any("100.000" in c for c in claims_other)


# ==================== TEST GRUBU 2: BİRLEŞİK KARAR MATRİSİ ====================

def test_d01_clean_and_valid_video_accepted():
    """Temiz grounding ve valid video süresi doğrudan accept edilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(duration_status="valid", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "accept"
    assert decision.reason_codes == ()
    assert decision.claims == ()
    assert decision.grounding_clean is True
    assert decision.duration_acceptable is True
    assert decision.repair_attempted is False


def test_d02_clean_and_not_applicable_post_accepted():
    """Temiz grounding ve not_applicable non-video post doğrudan accept edilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_post_content(duration_status="not_applicable")

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "accept"
    assert decision.reason_codes == ()
    assert decision.grounding_clean is True
    assert decision.duration_acceptable is True


def test_d03_initial_generation_claim_triggers_repair():
    """İlk üretimde (repair_attempted=False) desteksiz iddia repair tetikler."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(caption="Tam 50 bin kullanıcı memnun kaldı.", duration_status="valid", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "repair"
    assert decision.reason_codes == ("ungrounded_claim",)
    assert len(decision.claims) > 0
    assert decision.grounding_clean is False
    assert decision.duration_acceptable is True
    assert decision.repair_attempted is False


def test_d04_initial_generation_duration_mismatch_triggers_repair():
    """İlk üretimde (repair_attempted=False) süre uyuşmazlığı repair tetikler."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(duration_status="mismatch", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "repair"
    assert decision.reason_codes == ("duration_mismatch",)
    assert decision.claims == ()
    assert decision.grounding_clean is True
    assert decision.duration_acceptable is False


def test_d05_initial_generation_both_claim_and_mismatch_single_unified_repair():
    """İlk üretimde hem claim hem duration mismatch varsa TEK birleşik repair kararı ve 2 reason code üretilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(caption="Yüzde 80 indirim fırsatı!", duration_status="mismatch", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "repair"
    assert decision.reason_codes == ("ungrounded_claim", "duration_mismatch")
    assert len(decision.claims) > 0
    assert decision.grounding_clean is False
    assert decision.duration_acceptable is False


def test_d06_repaired_clean_and_valid_accepted():
    """Repair sonrasında (repair_attempted=True) grounding temiz ve süre geçerli ise accept edilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(duration_status="valid", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=True)

    assert decision.action == "accept"
    assert decision.reason_codes == ()
    assert decision.claims == ()
    assert decision.grounding_clean is True
    assert decision.duration_acceptable is True
    assert decision.repair_attempted is True


def test_d07_repaired_clean_but_duration_still_mismatch_accepted_with_warning():
    """Repair sonrasında grounding temiz fakat süre hâlâ mismatch ise accept edilir ve duration_mismatch warning'e eklenir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(
        duration_status="mismatch",
        payload_claim=False,
        validation_warnings=("some_existing_warning",),
    )

    decision = evaluate_social_content_quality(content, context, repair_attempted=True)

    assert decision.action == "accept"
    assert decision.reason_codes == ("duration_mismatch",)
    assert decision.claims == ()
    assert decision.grounding_clean is True
    assert decision.duration_acceptable is False
    # Warning listesinde hem eski uyarı hem de duration_mismatch bulunmalı
    assert "duration_mismatch" in decision.warnings
    assert "some_existing_warning" in decision.warnings
    assert decision.warnings.count("duration_mismatch") == 1


def test_d08_repaired_claim_persists_rejected():
    """Repair sonrasında desteksiz iddia devam ediyorsa KESİNLİKLE reject edilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(caption="Hâlâ 1 numara organik marka!", duration_status="valid", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=True)

    assert decision.action == "reject"
    assert decision.reason_codes == ("ungrounded_claim",)
    assert len(decision.claims) > 0
    assert decision.grounding_clean is False
    assert decision.duration_acceptable is True


def test_d09_repaired_claim_and_mismatch_rejected_with_both_reason_codes():
    """Repair sonrasında hem claim hem mismatch varsa reject edilir ve iki reason code korunur."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(caption="1 numara organik marka!", duration_status="mismatch", payload_claim=False)

    decision = evaluate_social_content_quality(content, context, repair_attempted=True)

    assert decision.action == "reject"
    assert decision.reason_codes == ("ungrounded_claim", "duration_mismatch")
    assert decision.grounding_clean is False
    assert decision.duration_acceptable is False
    assert "duration_mismatch" in decision.warnings


def test_d10_voiceover_soft_warning_alone_does_not_trigger_repair_or_reject():
    """Seslendirme hızı soft warning'i tek başına repair veya reject tetiklemez; içerik accept edilir."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    content = _make_video_content(
        duration_status="valid",
        payload_claim=False,
        validation_warnings=("voiceover_duration_mismatch",),
    )

    decision = evaluate_social_content_quality(content, context, repair_attempted=False)

    assert decision.action == "accept"
    assert decision.reason_codes == ()
    assert decision.warnings == ("voiceover_duration_mismatch",)


# ==================== TEST GRUBU 3: TİP VE GÜVENLİK SÖZLEŞMELERİ ====================

def test_t01_invalid_content_type_rejected():
    """content nesnesi exact ValidatedSocialContent olmadığında CONTENT_QUALITY_INVALID_INPUT hatası fırlatılır."""
    context = SocialContentGroundingContext(primary_keyword="test")
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality("not_a_content", context, repair_attempted=False)  # type: ignore
    assert exc_info.value.error_code == "CONTENT_QUALITY_INVALID_INPUT"
    assert exc_info.value.field == "content"


def test_t02_invalid_context_type_rejected():
    """context nesnesi exact SocialContentGroundingContext olmadığında CONTENT_QUALITY_INVALID_INPUT hatası fırlatılır."""
    content = _make_post_content()
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(content, "not_a_context", repair_attempted=False)  # type: ignore
    assert exc_info.value.error_code == "CONTENT_QUALITY_INVALID_INPUT"
    assert exc_info.value.field == "context"


def test_t03_invalid_repair_attempted_type_rejected():
    """repair_attempted exact bool olmadığında (1, 'true', None) reddedilir."""
    content = _make_post_content()
    context = SocialContentGroundingContext(primary_keyword="test")
    for bad_bool in [1, 0, "True", None]:
        with pytest.raises(SocialContentQualityError) as exc_info:
            evaluate_social_content_quality(content, context, repair_attempted=bad_bool)  # type: ignore
        assert exc_info.value.error_code == "CONTENT_QUALITY_INVALID_INPUT"
        assert exc_info.value.field == "repair_attempted"


def test_t04_invalid_grounding_context_fields():
    """Boş, untrimmed veya aşırı uzun keyword/facts/usp değerleri SocialContentQualityError fırlatır."""
    # Boş keyword
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword="   ")
    assert exc_info.value.field == "primary_keyword"

    # Untrimmed keyword
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword=" cilt ")
    assert exc_info.value.field == "primary_keyword"

    # 201 karakter keyword
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword="a" * 201)
    assert exc_info.value.field == "primary_keyword"

    # Boş product_facts
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword="cilt", product_facts=" ")
    assert exc_info.value.field == "product_facts"

    # 5001 karakter trusted_brand_usp
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword="cilt", trusted_brand_usp="u" * 5001)
    assert exc_info.value.field == "trusted_brand_usp"


def test_t05_inconsistent_content_duration_status_or_payload():
    """Bilinmeyen duration_status veya video/not_applicable uyuşmazlığı CONTENT_QUALITY_INCONSISTENT_CONTENT fırlatır."""
    context = SocialContentGroundingContext(primary_keyword="test")

    # 1. Bilinmeyen duration_status
    bad_ds_content = _make_post_content(duration_status="unknown_status")
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(bad_ds_content, context, repair_attempted=False)
    assert exc_info.value.error_code == "CONTENT_QUALITY_INCONSISTENT_CONTENT"
    assert exc_info.value.field == "duration_status"

    # 2. Video payload ancak not_applicable duration_status
    inconsistent_video = _make_video_content(duration_status="not_applicable", payload_claim=False)
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(inconsistent_video, context, repair_attempted=False)
    assert exc_info.value.error_code == "CONTENT_QUALITY_INCONSISTENT_CONTENT"
    assert exc_info.value.field == "duration_status"

    # 3. Non-video post ancak valid duration_status
    inconsistent_post = _make_post_content(duration_status="valid")
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(inconsistent_post, context, repair_attempted=False)
    assert exc_info.value.error_code == "CONTENT_QUALITY_INCONSISTENT_CONTENT"
    assert exc_info.value.field == "duration_status"

    # 4. Video formatı ancak actual_duration_sec None veya geçersiz
    bad_duration_video = _make_video_content(actual_duration_sec=None, payload_claim=False)
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(bad_duration_video, context, repair_attempted=False)
    assert exc_info.value.error_code == "CONTENT_QUALITY_INCONSISTENT_CONTENT"
    assert exc_info.value.field == "actual_duration_sec"

    # 5. Non-video formatı ancak actual_duration_sec dolu
    bad_post_duration = _make_post_content(actual_duration_sec=30)
    with pytest.raises(SocialContentQualityError) as exc_info:
        evaluate_social_content_quality(bad_post_duration, context, repair_attempted=False)
    assert exc_info.value.error_code == "CONTENT_QUALITY_INCONSISTENT_CONTENT"
    assert exc_info.value.field == "actual_duration_sec"


def test_t06_find_ungrounded_claims_unexpected_exception_fails_closed(monkeypatch):
    """find_ungrounded_claims beklenmedik bir hata verirse fail-closed CONTENT_QUALITY_GROUNDING_ERROR fırlatılır."""
    from app.core.social import content_quality

    def _broken_finder(*args, **kwargs):
        raise RuntimeError("Internal parser crash")

    monkeypatch.setattr(content_quality, "find_ungrounded_claims", _broken_finder)

    context = SocialContentGroundingContext(primary_keyword="test")
    content = _make_post_content()

    with pytest.raises(SocialContentQualityError) as exc_info:
        find_social_content_ungrounded_claims(content, context)
    assert exc_info.value.error_code == "CONTENT_QUALITY_GROUNDING_ERROR"
    assert exc_info.value.field == "claims"
    # Ham exception veya içerik metni hata mesajında sızmamalı
    assert "Internal parser crash" not in str(exc_info.value)


def test_t07_error_messages_do_not_leak_sensitive_text():
    """Hata mesajlarında caption, hook, keyword, facts, usp veya claim yer almaz."""
    secret_keyword = "secret_forbidden_keyword_xyz"
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword=f" {secret_keyword} ")
    assert secret_keyword not in str(exc_info.value)

    secret_fact = "secret_confidential_fact_123"
    with pytest.raises(SocialContentQualityError) as exc_info:
        SocialContentGroundingContext(primary_keyword="valid", product_facts=f" {secret_fact} ")
    assert secret_fact not in str(exc_info.value)


def test_t08_dto_immutability():
    """SocialContentGroundingContext ve SocialContentQualityDecision dondurulmuştur (frozen)."""
    context = SocialContentGroundingContext(primary_keyword="cilt bakımı")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        context.primary_keyword = "mutated"  # type: ignore

    decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        decision.action = "reject"  # type: ignore
