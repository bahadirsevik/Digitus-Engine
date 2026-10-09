"""
Seçim güvenilirliği testleri (run-15 rework'u).

Kapsam: intent eksik-ID/parse retry + source işaretleme, prefilter tekil
retry sınırı + öncelik, şema/token kwargs sözleşmesi, transferlere gerçek
GT/GA, metadata'nın seçime dokunamaması, kapasite-odaklı expansion + HARD
bütçe, sıfır bütçede katman fallback'leri + fiyat filtresi garantisi.
"""
import json
import re
from decimal import Decimal

import pytest

from app.core.channel.ai_budget import AiCallBudget
from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
from app.core.constants import (
    FALLBACK_QUARANTINE_REASON,
    INTENT_MAX_TOKENS,
    INTENT_SOURCE_FALLBACK,
    INTENT_SOURCE_TRANSFER_AI,
    PREFILTER_MAX_TOKENS,
    SEO_KEEP_DEFAULT_REASON,
    SEO_METADATA_REASON,
    SEO_METADATA_UNAVAILABLE_REASON,
    SINGLE_RETRY_MAX_PER_STAGE,
    STOP_CAPACITY_REACHED,
)
from app.database.models import (
    ChannelCandidate,
    IntentAnalysis,
    KeywordScore,
    PreFilterResult,
)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _intent_prompt_ids(prompt: str):
    ids = []
    for line in (prompt or "").splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            raw = line[2:].split(":", 1)[0].strip()
            if raw.isdigit():
                ids.append(int(raw))
    return ids


def _json_prompt_ids(prompt: str):
    match = re.search(r"keywords=(\[.*\])", prompt or "", re.DOTALL)
    if not match:
        return []
    return [item["id"] for item in json.loads(match.group(1))]


def _seed_candidate(db, run_id, keyword_id, channel, rank, intent_passed=None):
    db.add(ChannelCandidate(
        scoring_run_id=run_id, keyword_id=keyword_id, channel=channel,
        raw_score=Decimal("1.0"), rank_in_channel=rank,
    ))
    if intent_passed is not None:
        db.add(IntentAnalysis(
            scoring_run_id=run_id, keyword_id=keyword_id, channel=channel,
            intent_type="commercial", confidence_score=Decimal("0.90"),
            ai_reasoning="seed", is_passed=intent_passed,
        ))


def _intent_row(db, run_id, keyword_id, channel):
    return (
        db.query(IntentAnalysis)
        .filter_by(scoring_run_id=run_id, keyword_id=keyword_id, channel=channel)
        .one()
    )


# ---------------------------------------------------------------------------
# Intent: eksik-ID retry / parse retry / kalıcı hata → fallback source
# ---------------------------------------------------------------------------


class MissingOnceIntentAI:
    """İlk çağrıda son keyword'ü atlar; sonraki çağrılarda istenen herkesi döner."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        ids = _intent_prompt_ids(prompt)
        if self.calls == 1 and len(ids) > 1:
            ids = ids[:-1]  # birini atla → targeted retry tetiklenir
        return json.dumps([
            {"keyword_id": kid, "intent_type": "commercial", "confidence": 0.9,
             "gt": 0, "ga": 1, "reasoning": "ok"}
            for kid in ids
        ])


def test_intent_missing_id_recovered_by_targeted_retry(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Intent Missing WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kws = [make_keyword(f"eksik test {i}", brand_profile_id=ws.id) for i in range(3)]
    for rank, kw in enumerate(kws, 1):
        _seed_candidate(db_session, run.id, kw.id, "SEO", rank)
    db_session.commit()

    ai = MissingOnceIntentAI()
    summary = IntentAnalyzer(db_session, ai).analyze_candidates(run.id, "SEO")

    # Eksik kelime targeted retry ile kurtarıldı — fallback YOK
    rows = db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=run.id, channel="SEO"
    ).all()
    assert len(rows) == 3
    assert all(row.source == "ai" for row in rows)
    assert all(row.ga is True for row in rows)
    assert summary["ai_calls_used"] == 2  # ilk çağrı + 1 targeted retry
    assert ai.calls == 2


class ParseErrorOnceIntentAI:
    """İlk çağrı bozuk JSON, ikincisi geçerli — parse hatası 1 retry almalı."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "BU GECERLI JSON DEGIL {"
        return json.dumps([
            {"keyword_id": kid, "intent_type": "commercial", "confidence": 0.9,
             "gt": 1, "ga": 0, "reasoning": "ok"}
            for kid in _intent_prompt_ids(prompt)
        ])


def test_intent_parse_error_gets_whole_batch_retry(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Intent Parse WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw = make_keyword("parse retry kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw.id, "SEO", 1)
    db_session.commit()

    IntentAnalyzer(db_session, ParseErrorOnceIntentAI()).analyze_candidates(run.id, "SEO")

    row = _intent_row(db_session, run.id, kw.id, "SEO")
    assert row.source == "ai"       # retry kurtardı, fallback değil
    assert row.gt is True


class AlwaysBrokenAI:
    def complete_json(self, prompt=None, **kwargs):
        return "HEP BOZUK ]["


def test_intent_permanent_failure_marks_fallback_and_seo_still_passes(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Intent Fallback WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw = make_keyword("kalici hata kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw.id, "SEO", 1)
    db_session.commit()

    IntentAnalyzer(db_session, AlwaysBrokenAI()).analyze_candidates(run.id, "SEO")

    row = _intent_row(db_session, run.id, kw.id, "SEO")
    assert row.source == INTENT_SOURCE_FALLBACK  # artık görünür
    assert row.is_passed is True                 # SEO intent ELEMEZ
    assert row.gt is None and row.ga is None     # NULL kalır (False değil)


# ---------------------------------------------------------------------------
# Prefilter: tekil retry sınırı + rank önceliği
# ---------------------------------------------------------------------------


class SingleOnlyAdsAI:
    """Çok kelimeli batch'lere boş sonuç; tek kelimelik çağrıya geçerli keep."""

    def __init__(self):
        self.single_calls = 0

    def complete_json(self, prompt=None, **kwargs):
        ids = _json_prompt_ids(prompt)
        if len(ids) != 1:
            return json.dumps({"results": []})
        self.single_calls += 1
        return json.dumps({"results": [{
            "keyword_id": ids[0], "decision": "keep", "label": "lead",
            "reason_code": "CATEGORY_SEARCH", "reason": "ok",
        }]})


def test_prefilter_single_retry_cap_prioritizes_top_ranks(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12 kelime hep eksik kalır → tekil kurtarma en üst 10 rank'ı kurtarır,
    kalan 2 karantina; sınır ÇAĞRI-başına toplamdır (batch başına değil)."""
    ws = make_workspace("Single Retry WS")
    run = make_scoring_run(brand_profile_id=ws.id, ads_capacity=20)
    kws = []
    for rank in range(1, 13):
        kw = make_keyword(f"tekil retry {rank:02d}", brand_profile_id=ws.id)
        kws.append(kw)
        _seed_candidate(db_session, run.id, kw.id, "ADS", rank, intent_passed=True)
    db_session.commit()

    ai = SingleOnlyAdsAI()
    summary = AdsPreFilter(db_session, ai).filter_candidates(run.id)

    assert ai.single_calls == SINGLE_RETRY_MAX_PER_STAGE  # tam 10 tekil çağrı
    assert summary["kept"] == 10
    assert summary["fallback"] == 2

    quarantined = {
        row.keyword_id
        for row in db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, channel="ADS", is_fallback=True
        )
    }
    # Öncelik rank'a göre: kurtarılamayanlar EN DÜŞÜK öncelikli 11-12
    assert quarantined == {kws[10].id, kws[11].id}
    for row in db_session.query(PreFilterResult).filter_by(
        scoring_run_id=run.id, channel="ADS", is_fallback=True
    ):
        assert (row.extra_data or {}).get("reason_code") == FALLBACK_QUARANTINE_REASON


# ---------------------------------------------------------------------------
# Şema / max_tokens kwargs sözleşmesi
# ---------------------------------------------------------------------------


class RecordingAI:
    def __init__(self, payload_fn):
        self.kwargs_seen = []
        self._payload_fn = payload_fn

    def complete_json(self, prompt=None, **kwargs):
        self.kwargs_seen.append(kwargs)
        return self._payload_fn(prompt)


def test_prefilter_and_intent_pass_schema_and_tokens(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Schema WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw = make_keyword("sema kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw.id, "ADS", 1, intent_passed=True)
    db_session.commit()

    ads_ai = RecordingAI(lambda p: json.dumps({"results": [{
        "keyword_id": _json_prompt_ids(p)[0], "decision": "keep",
        "label": "lead", "reason": "ok",
    }]}))
    AdsPreFilter(db_session, ads_ai).filter_candidates(run.id)
    assert ads_ai.kwargs_seen[0]["max_tokens"] == PREFILTER_MAX_TOKENS
    assert ads_ai.kwargs_seen[0]["response_schema"] is AdsPreFilter.RESPONSE_SCHEMA

    intent_ai = RecordingAI(lambda p: json.dumps([{
        "keyword_id": kid, "intent_type": "commercial", "confidence": 0.9,
        "gt": 0, "ga": 0, "reasoning": "ok",
    } for kid in _intent_prompt_ids(p)]))
    # SEO kanalı için ayrı candidate gerekli
    kw2 = make_keyword("sema seo kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw2.id, "SEO", 1)
    db_session.commit()
    IntentAnalyzer(db_session, intent_ai).analyze_candidates(run.id, "SEO")
    assert intent_ai.kwargs_seen[0]["max_tokens"] == INTENT_MAX_TOKENS
    schema = intent_ai.kwargs_seen[0]["response_schema"]
    item_required = schema["properties"]["results"]["items"]["required"]
    assert "gt" in item_required and "ga" in item_required


# ---------------------------------------------------------------------------
# Transfer: gerçek GT/GA + mevcut SEO kayıtları overwrite edilmez
# ---------------------------------------------------------------------------


class TransferIntentAI:
    def complete_json(self, prompt=None, **kwargs):
        return json.dumps([
            {"keyword_id": kid, "intent_type": "informational", "confidence": 0.8,
             "gt": 1, "ga": 0, "reasoning": "transfer analizi"}
            for kid in _intent_prompt_ids(prompt)
        ])


def test_transfer_gets_real_gt_ga_and_existing_seo_rows_untouched(
    db_session, make_workspace, make_scoring_run, make_keyword, make_keyword_score
):
    ws = make_workspace("Transfer WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw_new = make_keyword("yeni transfer kelime", brand_profile_id=ws.id)
    kw_existing = make_keyword("zaten seo kelime", brand_profile_id=ws.id)

    for kw in (kw_new, kw_existing):
        make_keyword_score(
            scoring_run_id=run.id, keyword_id=kw.id,
            seo_score=Decimal("10.0"), seo_rank=1,
        )
        # ADS'te elenmiş + SEO'ya transfer işaretli
        db_session.add(PreFilterResult(
            scoring_run_id=run.id, keyword_id=kw.id, channel="ADS",
            is_kept=False, ai_class=-1, ai_reasoning="elendi",
            extra_data={"reason_code": "LOW_COMMERCIAL_VALUE"},
            transfer_channel="SEO", is_fallback=False,
        ))
    # kw_existing zaten SEO adayı + intent'i var (gt=False ile ayırt edilir)
    _seed_candidate(db_session, run.id, kw_existing.id, "SEO", 1)
    db_session.add(IntentAnalysis(
        scoring_run_id=run.id, keyword_id=kw_existing.id, channel="SEO",
        intent_type="informational", confidence_score=Decimal("0.70"),
        ai_reasoning="mevcut", is_passed=True, gt=False, ga=False, source="ai",
    ))
    db_session.commit()

    ai = TransferIntentAI()
    engine = ChannelEngine(db_session, ai)
    result = engine._process_cross_channel_transfers(
        run.id, SeoPreFilter(db_session, ai)
    )

    assert result["requested"] == 2
    assert result["created"] == 1               # yalnız kw_new
    assert result["already_present"] == 1
    assert result["intent_ai_created"] == 1
    assert result["intent_fallback_created"] == 0

    # Yeni transfer: GERÇEK gt/ga + transfer_ai source
    new_row = _intent_row(db_session, run.id, kw_new.id, "SEO")
    assert new_row.source == INTENT_SOURCE_TRANSFER_AI
    assert new_row.gt is True
    assert new_row.is_passed is True

    # Mevcut SEO kaydı OVERWRITE EDİLMEDİ
    existing_row = _intent_row(db_session, run.id, kw_existing.id, "SEO")
    assert existing_row.source == "ai"
    assert existing_row.gt is False
    assert existing_row.ai_reasoning == "mevcut"

    # SEO prefilter (deterministik) transfer için keep üretti
    pf = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_new.id, channel="SEO")
        .one()
    )
    assert pf.is_kept is True
    assert (pf.extra_data or {}).get("reason_code") == SEO_KEEP_DEFAULT_REASON


# ---------------------------------------------------------------------------
# Metadata: seçime dokunamaz + önceki başarı ezilmez
# ---------------------------------------------------------------------------


class AdversarialMetadataAI:
    """kw listesindeki İLK kelimeye düşmanca (is_kept=false'lu) metadata döner,
    diğerlerine hiçbir şey dönmez (unavailable senaryosu)."""

    def __init__(self, answer_for: int):
        self.answer_for = answer_for

    def complete_json(self, prompt=None, **kwargs):
        ids = _json_prompt_ids(prompt)
        if self.answer_for in ids:
            return json.dumps({"results": [{
                "keyword_id": self.answer_for,
                "depth_label": "shallow",
                "is_kept": False,       # düşmanca alan — yok sayılmalı
                "decision": "eliminate", # düşmanca alan — yok sayılmalı
                "reason": "metadata",
                "meta": {"geo_suitable": True, "h1_suggestion": "H1 Deneme",
                         "h2_suggestions": ["Alt 1", "Alt 2"]},
            }]})
        return json.dumps({"results": []})


def test_metadata_cannot_flip_is_kept_and_prior_success_not_clobbered(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Metadata WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw_ok = make_keyword("metadata basarili", brand_profile_id=ws.id)
    kw_fail = make_keyword("metadata basarisiz", brand_profile_id=ws.id)
    for kw in (kw_ok, kw_fail):
        db_session.add(PreFilterResult(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            is_kept=True, label=None, ai_reasoning="deterministik keep",
            extra_data={"reason_code": SEO_KEEP_DEFAULT_REASON},
            is_fallback=False,
        ))
    db_session.commit()

    prefilter = SeoPreFilter(db_session, AdversarialMetadataAI(kw_ok.id))
    result = prefilter.generate_metadata(run.id, [kw_ok.id, kw_fail.id])

    assert result["updated"] == 1
    assert result["unavailable"] == 1
    db_session.expire_all()

    ok_row = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_ok.id, channel="SEO")
        .one()
    )
    # Düşmanca is_kept/decision alanları SEÇİMİ DEĞİŞTİREMEDİ
    assert ok_row.is_kept is True
    assert ok_row.label == "shallow"
    assert (ok_row.extra_data or {}).get("reason_code") == SEO_METADATA_REASON
    assert (ok_row.extra_data or {}).get("h1_suggestion") == "H1 Deneme"

    fail_row = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_fail.id, channel="SEO")
        .one()
    )
    assert fail_row.is_kept is True  # unavailable eleme DEĞİLDİR
    assert (fail_row.extra_data or {}).get("reason_code") == SEO_METADATA_UNAVAILABLE_REASON

    # İkinci koşuda AI tamamen çöker → önceki BAŞARILI metadata EZİLMEZ
    result2 = SeoPreFilter(db_session, AlwaysBrokenAI()).generate_metadata(
        run.id, [kw_ok.id, kw_fail.id]
    )
    assert result2["updated"] == 0
    db_session.expire_all()
    ok_row2 = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_ok.id, channel="SEO")
        .one()
    )
    assert (ok_row2.extra_data or {}).get("reason_code") == SEO_METADATA_REASON
    assert (ok_row2.extra_data or {}).get("h1_suggestion") == "H1 Deneme"


# ---------------------------------------------------------------------------
# Kapasite-odaklı expansion (%70 eşiği yok) + HARD bütçe
# ---------------------------------------------------------------------------


class KeepLeadAI:
    """Tüm prompt tiplerine geçerli cevap (ADS keep lead)."""

    def complete_json(self, prompt=None, **kwargs):
        if "intent_type" in (prompt or ""):
            return json.dumps([
                {"keyword_id": kid, "intent_type": "commercial", "confidence": 0.9,
                 "reasoning": "ok"}
                for kid in _intent_prompt_ids(prompt)
            ])
        if "is_brand_relevant" in (prompt or ""):
            return json.dumps({"results": [
                {"keyword_id": kid, "is_brand_relevant": True,
                 "matched_exclude_theme": None, "reason": "ok"}
                for kid in _json_prompt_ids(prompt)
            ]})
        return json.dumps({"results": [
            {"keyword_id": kid, "decision": "keep", "label": "lead",
             "reason_code": "CATEGORY_SEARCH", "reason": "ok"}
            for kid in _json_prompt_ids(prompt)
        ]})


def test_expansion_fills_past_old_threshold_to_capacity(
    db_session, make_workspace, make_scoring_run, make_keyword, make_keyword_score
):
    """8/10 dolu kanal (eski %70 eşiği SAĞLANMIŞTI) artık expand eder ve
    kapasiteye ulaşır; bütçe gerçek ai_calls_used ile korunur."""
    ws = make_workspace("Capacity WS", profile_data={"exclude_themes": []})
    run = make_scoring_run(
        brand_profile_id=ws.id, ads_capacity=10,
        enable_seo=False, enable_social=False,
    )
    keywords = []
    for idx in range(60):
        kw = make_keyword(f"kapasite kelime {idx:03d}", brand_profile_id=ws.id)
        keywords.append(kw)
        make_keyword_score(
            scoring_run_id=run.id, keyword_id=kw.id,
            ads_score=Decimal(str(1000 - idx)), ads_rank=idx + 1,
        )

    engine = ChannelEngine(db_session, KeepLeadAI())
    engine.pool_builder.build_candidate_pools(run.id)  # cap: 10*3=30 aday

    # İlk 30 adaydan yalnız 8'i kept olacak şekilde seed
    for idx, kw in enumerate(keywords[:30]):
        db_session.add(IntentAnalysis(
            scoring_run_id=run.id, keyword_id=kw.id, channel="ADS",
            intent_type="commercial", confidence_score=Decimal("0.90"),
            ai_reasoning="seed", is_passed=True,
        ))
        db_session.add(PreFilterResult(
            scoring_run_id=run.id, keyword_id=kw.id, channel="ADS",
            is_kept=idx < 8, label="lead" if idx < 8 else None,
            ai_class=1 if idx < 8 else -1, ai_reasoning="seed",
            extra_data={"reason_code": "SEED"}, is_fallback=False,
        ))
    db_session.commit()

    final_counts = engine._build_final_pools_v2(run.id, run)
    assert final_counts["ADS"] == 8  # eski eşik ceil(10*0.7)=7 → expand ETMEZDİ

    report = engine._run_expansion_rounds(
        run.id, run, final_counts, relevance_coefficient=1.0
    )

    assert report["ADS"]["rounds_run"] >= 1
    assert report["ADS"]["final_count_after_expansion"] == 10
    assert report["ADS"]["unfilled_count"] == 0
    assert report["ADS"]["stop_reason"] == STOP_CAPACITY_REACHED
    assert (
        report["ADS"]["expansion_ai_batches_used"]
        <= report["ADS"]["expansion_ai_batch_budget"]
    )


# ---------------------------------------------------------------------------
# Sıfır bütçe: katman fallback'leri + fiyat filtresi garantisi
# ---------------------------------------------------------------------------


def test_zero_budget_seo_price_filter_still_works(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Zero Budget SEO WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw_price = make_keyword("robot supurge fiyat", brand_profile_id=ws.id)
    kw_info = make_keyword("robot supurge secimi", brand_profile_id=ws.id)
    for rank, kw in enumerate((kw_price, kw_info), 1):
        _seed_candidate(db_session, run.id, kw.id, "SEO", rank, intent_passed=True)
    db_session.commit()

    summary = SeoPreFilter(db_session, AlwaysBrokenAI()).filter_candidates(
        run.id, budget=AiCallBudget(0)
    )

    # Deterministik yol AI'sız çalıştı: PRICE_TERM bypass EDİLMEDİ
    assert summary["ai_calls_used"] == 0
    pf = {
        row.keyword_id: row
        for row in db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, channel="SEO"
        )
    }
    assert pf[kw_price.id].is_kept is False
    assert (pf[kw_price.id].extra_data or {}).get("reason_code") == "PRICE_TERM"
    assert pf[kw_info.id].is_kept is True


def test_zero_budget_ads_quarantines_and_intent_falls_back(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Zero Budget ADS WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kw = make_keyword("butcesiz kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw.id, "ADS", 1, intent_passed=True)
    kw_seo = make_keyword("butcesiz seo kelime", brand_profile_id=ws.id)
    _seed_candidate(db_session, run.id, kw_seo.id, "SEO", 1)
    db_session.commit()

    # ADS prefilter: bütçe 0 → karantina (kanal fallback semantiği)
    summary = AdsPreFilter(db_session, KeepLeadAI()).filter_candidates(
        run.id, budget=AiCallBudget(0)
    )
    assert summary["ai_calls_used"] == 0
    assert summary["fallback"] == 1 and summary["kept"] == 0

    # Intent: bütçe 0 → fallback source; SEO yine GEÇER
    IntentAnalyzer(db_session, KeepLeadAI()).analyze_candidates(
        run.id, "SEO", budget=AiCallBudget(0)
    )
    row = _intent_row(db_session, run.id, kw_seo.id, "SEO")
    assert row.source == INTENT_SOURCE_FALLBACK
    assert row.is_passed is True
