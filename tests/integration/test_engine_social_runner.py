# -*- coding: utf-8 -*-
"""SOCIAL V5 kosucusu entegrasyon testleri (`app/core/engine/social/runner.py`).

QA gorevi: Motor v3 Faz 5 = SOCIAL V5. Butun AI etkilesimi SAHTE bir istemciyle
kurulur (`FakeSocialAI`); GERCEK saglayiciya HICBIR cagri yapilmaz. Akis:
relevance (SEO ile AYNI sablon, batch 10) -> Rel>=40 kapisi -> V4 dort boyut
(batch 15) -> RF>=50 & BC>=50 kapisi -> V5 niyet (batch 15) -> SocialScore ->
Priority -> sira -> havuz. Girdi DONMUS evrenden (`freeze_universe_snapshot`)
gelir.

Faz 3 (`tests/integration/test_engine_ads_runner.py`) ve K17
(`tests/unit/test_engine_ai_runner.py`) ile AYNI kalip kullanilir:
`for_stage(stage, model=..., thinking_level=...)` StageScopedAIService
deseni + sirali hazir cevap kuyrugu, bos kuyrukta AssertionError (gercek
saglayiciya kaymayi engeller).

DUZELTME (koordinator, 17.09): daha once bu dosyada raporlanan iki kusur
uretim kodunda giderildi:
  1. `ai_runner.run_batch` artik `result_key`/`response_id_field`
     parametreleri aliyor. `social/runner.py::_stage`, V4/V5 asamalarini
     `result_key=None, response_id_field="keyword_id"` ile cagiriyor —
     yani SOCIAL'in KENDI ilan ettigi semasiyla (`P.RESPONSE_SCHEMA`: duz
     DIZI + `keyword_id` alani, `{"results":...}` SARMALI YOK) birebir
     uyumlu bir cevap artik basariyla parse ediliyor. Relevance asamasi
     DEGISMEDI: hala SEO kalibinda (`{"results": [{"id": ...}]}`).
     Bu dosyadaki TUM `_working_v4_response`/`_working_v5_response`
     yardimcilari artik GERCEK semayi (duz dizi + `keyword_id`) uretir —
     eskiden "calisir hale getirmek icin" kullanilan SEO/ADS kalibi
     kaldirildi, cunku artik gercekten calisan bicim BUDUR.
  2. Ayni cevapta TEKRAR EDEN id artik yapisal hata (`AiStageError`) —
     eskiden sessizce "sonuncu kazanir"di (bkz. `test_engine_ai_runner.py`).
  3. `pipeline.compute_social_list` sifir relevance-survivor'da artik
     COKMUYOR: bos havuz + `empty_reason="no_relevance_survivors"` doner
     (bkz. `test_no_survivors_skips_v4_and_v5_entirely_and_returns_empty_
     pool_without_raising`).
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

import pytest

from app.core.engine import context as CTX
from app.core.engine.ai_runner import AiStageError
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    MANIFEST_KEY,
    StageContext,
    StageContextMismatch,
    manifest_section,
    seal_manifest,
    verify_stage_context,
)
from app.core.engine.social import pipeline as PL
from app.core.engine.social import prompts as P
from app.core.engine.seo import prompts as SEOP
from app.core.engine.social.priority import (
    EXCLUDE_REASON_COMMERCIAL,
    EXCLUDE_REASON_GATE,
    EXCLUDE_REASON_LOW_SOCIAL_SCORE,
    PRIORITY_EXCLUDE,
    PRIORITY_PRIMARY,
    PRIORITY_SECONDARY,
    PRIORITY_TREND_CONTENT,
)
from app.core.engine.social.runner import (
    SCOPE_KEYWORD,
    SOCIAL_MODEL,
    SOCIAL_STAGES,
    STAGE_NORMBOUNDS,
    STAGE_RELEVANCE,
    STAGE_V4,
    STAGE_V5,
    SocialStageError,
    run_social_stage,
    social_models,
    social_prompt_shas,
)
from app.database.models import EngineStageResult

PROFILE = {
    "sector": "kozmetik",
    "brand_name": "Digitus Sac Bakim",
    "brand_summary": "Sac dokulmesi ve beyazlamaya yonelik urunler satan marka",
    "products": ["sac serumu", "sampuan"],
}


def _firm_sha(profile: Dict[str, Any] = PROFILE) -> str:
    return CTX.firm_block_sha256(CTX.firm_block(profile))


def _firm_text(profile: Dict[str, Any] = PROFILE) -> str:
    return CTX.firm_block(profile)


def _seal_social_manifest(run, *, firm_sha: str = None) -> None:
    seal_manifest(
        run,
        firm_block_sha256=firm_sha or _firm_sha(),
        algorithm_versions={"social": "social_v5_uretim_v1"},
        models=social_models(),
        prompt_shas=social_prompt_shas(),
        location_policy=_loc_snap({}),
    )


# ---------------------------------------------------------------------------
# Sahte AI istemcisi (ADS/K17 testleriyle AYNI kalip)
# ---------------------------------------------------------------------------


class _ScopedFakeSocialAI:
    def __init__(self, parent: "FakeSocialAI", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def complete_json(self, prompt: str, max_tokens: int = None,
                      response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond(self._stage, prompt)


class FakeSocialAI:
    """`complete_json` cagrilarini SAYAN + sirayla hazir cevap dondüren
    sahte istemci. GERCEK saglayiciya ASLA gitmez.

    Kuyruktaki oge `BaseException` ORNEGIYSE oldugu gibi firlatilir; aksi
    halde HAM STRING JSON cevap olarak dondurulur.
    """

    def __init__(self, stage_responses: Dict[str, List[Any]]) -> None:
        self._queues: Dict[str, List[Any]] = {
            stage: list(payloads) for stage, payloads in stage_responses.items()
        }
        self.calls: List[tuple] = []
        self.call_counts: Counter = Counter()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "_ScopedFakeSocialAI":
        return _ScopedFakeSocialAI(self, stage)

    def _respond(self, stage: str, prompt: str) -> str:
        self.calls.append((stage, prompt))
        self.call_counts[stage] += 1
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"FakeSocialAI: '{stage}' icin kuyrukta hazir cevap kalmadi "
                f"(bu {self.call_counts[stage]}. cagri) — GERCEK saglayiciya "
                "gidilmeye CALISILIYOR olabilir, bu test bunu engeller."
            )
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def total_calls(self) -> int:
        return sum(self.call_counts.values())


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _working_relevance_response(items: Dict[int, Dict[str, Any]]) -> str:
    def band_for(value: float) -> str:
        for name, (lo, hi) in SEOP.REL_BANDS.items():
            if lo <= value <= hi:
                return name
        raise AssertionError(f"test relevance degeri bant disinda: {value}")

    return _ok({"results": [
        {"id": kid, "band": v.get("band") or band_for(v["relevance"]),
         "relevance": v["relevance"]}
        for kid, v in items.items()
    ]})


def _working_v4_response(items: Dict[int, Dict[str, Any]]) -> str:
    """SOCIAL'in GERCEK semasi (`P.RESPONSE_SCHEMA`): ust duzey DIZI +
    `keyword_id` alani, `{"results":...}` SARMALI YOK. `social/runner.py`
    artik `result_key=None, response_id_field="keyword_id"` ile bunu dogru
    okur."""
    return json.dumps([{"keyword_id": kid, **fields} for kid, fields in items.items()],
                      ensure_ascii=False)


def _working_v5_response(items: Dict[int, Dict[str, Any]]) -> str:
    """SOCIAL'in GERCEK semasi (`P.V5_RESPONSE_SCHEMA`) — bkz. yukarisi."""
    return json.dumps([{"keyword_id": kid, **fields} for kid, fields in items.items()],
                      ensure_ascii=False)


# ---------------------------------------------------------------------------
# Ortak kurulum: donmus evren, 6 kelime — relevance/RF-BC/intent dallarinin
# HEPSINI kapsayacak sekilde tasarlandi (yerel olarak `pipeline.
# compute_social_list` ile onceden dogrulandi):
#   kw1: relevance < 40      -> V4'e HIC gitmez
#   kw2: relevance>=40, RF<50-> V4'e gider, uc kapida ELENIR (V5'e gitmez)
#   kw3: uc kapiyi gecer, CONTENT_NATIVE, SS yuksek -> PRIMARY
#   kw4: uc kapiyi gecer, COMMERCIAL_SEARCH          -> EXCLUDE(commercial)
#   kw5: uc kapiyi gecer, TREND_OPPORTUNITY          -> TREND_CONTENT
#   kw6: uc kapiyi gecer, CONTENT_NATIVE, SS dusuk   -> EXCLUDE(low_social_score)
# kept sirasi: [kw3, kw5] (PRIMARY once, sonra TREND_CONTENT)
# ---------------------------------------------------------------------------

KEYWORD_SPECS = [
    # text, volume, trend_3m(%), trend_12m(%)
    ("kw eksik relevance", 500, 5.0, 2.0),
    ("kw dusuk rf", 800, 10.0, 5.0),
    ("kw primary yuksek", 5000, 120.0, 40.0),
    ("kw commercial elenir", 3000, 60.0, 20.0),
    ("kw trend firsat", 2000, 80.0, 10.0),
    ("kw dusuk skor", 100, -50.0, -60.0),
]

RELEVANCE_BY_INDEX = [0.20, 0.60, 0.90, 0.85, 0.75, 0.55]

DIMS_BY_INDEX = {
    1: {"brand_contentability": 80, "attention": 70, "scenario": 60, "relative_fit": 30},
    2: {"brand_contentability": 90, "attention": 85, "scenario": 80, "relative_fit": 90},
    3: {"brand_contentability": 70, "attention": 60, "scenario": 55, "relative_fit": 65},
    4: {"brand_contentability": 60, "attention": 55, "scenario": 50, "relative_fit": 55},
    5: {"brand_contentability": 55, "attention": 50, "scenario": 50, "relative_fit": 55},
}

INTENTS_BY_INDEX = {
    2: "CONTENT_NATIVE",
    3: "COMMERCIAL_SEARCH",
    4: "TREND_OPPORTUNITY",
    5: "CONTENT_NATIVE",
}


def _make_frozen_universe(db_session, make_workspace, make_scoring_run, make_keyword):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    keywords = []
    for text, volume, t3, t12 in KEYWORD_SPECS:
        kw = make_keyword(text, brand_profile_id=workspace.id,
                          monthly_volume=volume, trend_3m=t3, trend_12m=t12)
        keywords.append(kw)
    db_session.commit()
    universe = CTX.freeze_universe_snapshot(db_session, run)
    db_session.commit()
    return workspace, run, keywords, universe


def _relevance_items(keywords) -> Dict[int, Dict[str, Any]]:
    return {kw.id: {"relevance": r, "band": None}
           for kw, r in zip(keywords, RELEVANCE_BY_INDEX)}


def _v4_items(keywords) -> Dict[int, Dict[str, Any]]:
    # yalniz relevance>=40 gecenler (idx 1..5) icin AI cagrilir
    return {keywords[i].id: dict(fields) for i, fields in DIMS_BY_INDEX.items()}


def _v5_items(keywords) -> Dict[int, Dict[str, Any]]:
    # yalniz uc kapiyi (Rel/RF/BC) gecenler (idx 2..5) icin AI cagrilir
    return {keywords[i].id: {"social_intent_type": cls, "intent_confidence": 90,
                             "intent_reason": "r"}
           for i, cls in INTENTS_BY_INDEX.items()}


def _full_ai(keywords) -> FakeSocialAI:
    return FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        STAGE_V4: [_working_v4_response(_v4_items(keywords))],
        STAGE_V5: [_working_v5_response(_v5_items(keywords))],
    })


# ---------------------------------------------------------------------------
# muhur sozlesmesi
# ---------------------------------------------------------------------------


def test_social_manifest_sealed_with_social_prompt_shas_and_models_matches_runner_context(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    _seal_social_manifest(run)
    db_session.commit()

    section = manifest_section(run)
    assert section["models"] == social_models()
    assert section["prompt_shas"] == social_prompt_shas()
    assert section["firm_block_sha256"] == _firm_sha()

    for stage in SOCIAL_STAGES:
        ctx = StageContext(model=social_models()[stage],
                           prompt_sha=social_prompt_shas()[stage],
                           firm_block_sha256=_firm_sha())
        verify_stage_context(run, stage, ctx)  # patlamamali


# ---------------------------------------------------------------------------
# Tam akis: relevance -> V4 (yalniz survivor) -> uc kapi -> V5 (yalniz
# eligible) -> havuz
# ---------------------------------------------------------------------------


def test_run_social_stage_full_flow_calls_each_ai_stage_with_the_correct_id_subset(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _full_ai(keywords)
    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    result = out["result"]

    # V4'e giden ID kumesi: TUM 6 kelime relevance'a gitti, yalniz 5'i (idx
    # 1..5, relevance>=40) V4'e gitti.
    expected_v4_ids = {keywords[i].id for i in range(1, 6)}
    assert {r["keyword_id"] for r in result["survivors"]} == expected_v4_ids
    assert keywords[0].id not in {r["keyword_id"] for r in result["survivors"]}

    # V5'e giden ID kumesi: yalniz uc kapiyi (RF/BC) gecen idx 2..5.
    expected_v5_ids = {keywords[i].id for i in range(2, 6)}
    assert {r["keyword_id"] for r in result["eligible"]} == expected_v5_ids
    assert keywords[1].id not in expected_v5_ids  # dusuk RF -> V5'e gitmedi

    # Nihai havuz: kw3 (PRIMARY) once, kw5 (TREND_CONTENT) sonra.
    pool_ids = [r["keyword_id"] for r in result["pool"]]
    assert pool_ids == [keywords[2].id, keywords[4].id]

    priority_by_id = {r["keyword_id"]: (r["social_priority"], r["exclude_reason"])
                      for r in result["final"]}
    assert priority_by_id[keywords[2].id] == (PRIORITY_PRIMARY, None)
    assert priority_by_id[keywords[4].id] == (PRIORITY_TREND_CONTENT, None)
    assert priority_by_id[keywords[3].id] == (PRIORITY_EXCLUDE, EXCLUDE_REASON_COMMERCIAL)
    assert priority_by_id[keywords[5].id] == (PRIORITY_EXCLUDE, EXCLUDE_REASON_LOW_SOCIAL_SCORE)
    assert priority_by_id[keywords[1].id] == (PRIORITY_EXCLUDE, EXCLUDE_REASON_GATE)
    assert priority_by_id[keywords[0].id] == (PRIORITY_EXCLUDE, EXCLUDE_REASON_GATE)

    assert ai.call_counts[STAGE_RELEVANCE] == 1
    assert ai.call_counts[STAGE_V4] == 1
    assert ai.call_counts[STAGE_V5] == 1


# ---------------------------------------------------------------------------
# Kapasite: pool_size kullanici kapasitesini uygular
# ---------------------------------------------------------------------------


def test_pool_size_parameter_truncates_pool_to_user_capacity(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _full_ai(keywords)
    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text(),
                           pool_size=1)
    result = out["result"]
    assert len(result["kept"]) == 2          # sirali liste DEGISMEDI
    assert len(result["pool"]) == 1          # havuz kullanici kapasitesine kirpildi
    assert result["pool"][0]["keyword_id"] == keywords[2].id


def test_default_pool_size_is_60():
    assert PL.DEFAULT_SOCIAL_POOL_SIZE == 60


# ---------------------------------------------------------------------------
# Asama atlaniyor mu: hic survivor yoksa V4 cagrilmaz; hic eligible yoksa
# V5 cagrilmaz
# ---------------------------------------------------------------------------


def test_no_survivors_skips_v4_and_v5_entirely_and_returns_empty_pool_without_raising(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """DUZELTME dogrulamasi: hicbir kelime Rel>=40 kapisini gecemezse
    `run_social_stage` artik `MissingSignal` ile COKMUYOR — bos havuz +
    `empty_reason='no_relevance_survivors'` donuyor (bu bir veri hatasi
    degil, gecerli bir is sonucu: marka/kelime evreni ortusmuyor)."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    # TUM kelimeler relevance kapisinin ALTINDA.
    all_low = {kw.id: {"relevance": 0.10, "band": None} for kw in keywords}
    ai = FakeSocialAI({STAGE_RELEVANCE: [_working_relevance_response(all_low)]})

    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    result = out["result"]
    assert result["survivors"] == []
    assert result["eligible"] == []
    assert result["kept"] == []
    assert result["pool"] == []
    assert result["bounds"] is None
    assert result.get("empty_reason") == "no_relevance_survivors"
    assert ai.call_counts[STAGE_RELEVANCE] == 1
    assert ai.call_counts[STAGE_V4] == 0
    assert ai.call_counts[STAGE_V5] == 0

    # Relevance asamasi kendisi basariyla tamamlandi ve kalicilastirildi;
    # V4/V5'e HIC girilmedigi icin o asamalara ait satir yok.
    assert len(_stage_rows(db_session, run, STAGE_RELEVANCE)) == len(keywords)
    assert _stage_rows(db_session, run, STAGE_V4) == []
    assert _stage_rows(db_session, run, STAGE_V5) == []


def test_survivors_but_none_pass_three_gate_skips_v5(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    all_high_rel = {kw.id: {"relevance": 0.90, "band": None} for kw in keywords}
    # HEPSI RF<50 -> uc kapida elenir, V5'e KIMSE gitmez.
    low_rf_dims = {kw.id: {"brand_contentability": 80, "attention": 70,
                          "scenario": 60, "relative_fit": 10}
                  for kw in keywords}
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(all_high_rel)],
        STAGE_V4: [_working_v4_response(low_rf_dims)],
    })

    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    result = out["result"]
    assert len(result["survivors"]) == 6
    assert result["eligible"] == []
    assert result["pool"] == []
    assert ai.call_counts[STAGE_V4] == 1
    assert ai.call_counts[STAGE_V5] == 0


# ---------------------------------------------------------------------------
# Eksik ID fail-closed — HER asamada: hedefli tekrardan sonra da eksikse
# hata + o asamaya ait EngineStageResult satiri YOK
# ---------------------------------------------------------------------------


def _stage_rows(db_session, run, stage: str):
    return (db_session.query(EngineStageResult)
           .filter(EngineStageResult.scoring_run_id == run.id,
                   EngineStageResult.stage == stage)
           .all())


def test_missing_id_after_targeted_retry_at_relevance_stage_raises_and_writes_no_rows(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    partial = {keywords[0].id: {"relevance": 0.9, "band": None}}  # 5 EKSIK
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(partial), _ok({"results": []})],
    })

    with pytest.raises(SocialStageError, match="checkpoint olusturulmaz"):
        run_social_stage(db_session, run=run, profile=PROFILE,
                         universe=universe.rows, ai=ai,
                         firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert _stage_rows(db_session, run, STAGE_RELEVANCE) == []
    assert ai.call_counts[STAGE_V4] == 0
    assert ai.call_counts[STAGE_V5] == 0


def test_missing_id_after_targeted_retry_at_v4_stage_raises_and_writes_no_v4_rows(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    survivor_ids = [keywords[i].id for i in range(1, 6)]
    partial_v4 = {survivor_ids[0]: DIMS_BY_INDEX[1]}  # 4 EKSIK
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        # hedefli tekrar de bos DIZI doner (SOCIAL'in gercek semasi — sarmal YOK).
        STAGE_V4: [_working_v4_response(partial_v4), json.dumps([])],
    })

    with pytest.raises(SocialStageError, match="checkpoint olusturulmaz"):
        run_social_stage(db_session, run=run, profile=PROFILE,
                         universe=universe.rows, ai=ai,
                         firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert len(_stage_rows(db_session, run, STAGE_RELEVANCE)) == 6  # basarili asama KORUNUR
    assert _stage_rows(db_session, run, STAGE_V4) == []
    assert ai.call_counts[STAGE_V5] == 0


def test_missing_id_after_targeted_retry_at_v5_stage_raises_and_writes_no_v5_rows(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    eligible_ids = [keywords[i].id for i in range(2, 6)]
    partial_v5 = {eligible_ids[0]: {"social_intent_type": "CONTENT_NATIVE",
                                    "intent_confidence": 90, "intent_reason": "r"}}
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        STAGE_V4: [_working_v4_response(_v4_items(keywords))],
        # hedefli tekrar de bos DIZI doner (SOCIAL'in gercek semasi — sarmal YOK).
        STAGE_V5: [_working_v5_response(partial_v5), json.dumps([])],
    })

    with pytest.raises(SocialStageError, match="checkpoint olusturulmaz"):
        run_social_stage(db_session, run=run, profile=PROFILE,
                         universe=universe.rows, ai=ai,
                         firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert len(_stage_rows(db_session, run, STAGE_RELEVANCE)) == 6
    assert len(_stage_rows(db_session, run, STAGE_V4)) == 5
    assert _stage_rows(db_session, run, STAGE_V5) == []


# ---------------------------------------------------------------------------
# Resume: ikinci cagri AI'yi TEKRAR cagirmaz, sonuc birebir aynidir
# ---------------------------------------------------------------------------


def test_run_social_stage_resume_does_not_call_ai_again_and_result_is_identical(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _full_ai(keywords)
    out1 = run_social_stage(db_session, run=run, profile=PROFILE,
                            universe=universe.rows, ai=ai,
                            firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    calls_after_first = ai.total_calls
    assert calls_after_first > 0

    # AYNI (artik BOS kuyruklu) sahte istemci: eger tekrar cagirmaya
    # CALISIRSA AssertionError firlatir — bu tekrar cagirilmadiginin en
    # sıkı kanitidir.
    out2 = run_social_stage(db_session, run=run, profile=PROFILE,
                            universe=universe.rows, ai=ai,
                            firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert ai.total_calls == calls_after_first, (
        "ikinci run_social_stage cagrisi AI'yi TEKRAR cagirdi — resume "
        "engine_stage_results'tan okumuyor"
    )
    assert out1["result"]["pool"] == out2["result"]["pool"]
    assert out1["result"]["final"] == out2["result"]["final"]

    rows_in_db = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .all()
    )
    assert rows_in_db, "hicbir asama satiri yazilmadi"
    assert {r.scope_type for r in rows_in_db} == {SCOPE_KEYWORD, "run"}
    assert {r.stage for r in rows_in_db} == {
        STAGE_RELEVANCE, STAGE_NORMBOUNDS, STAGE_V4, STAGE_V5}


# ---------------------------------------------------------------------------
# Baglam uyusmazligi: manifest degisirse resume SESSIZCE tekrar kullanilmaz
# ---------------------------------------------------------------------------


def test_run_social_stage_resume_with_manifest_drift_raises_stage_context_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _full_ai(keywords)
    run_social_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     ai=ai, firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    db_session.commit()

    manifest = dict(run.execution_manifest or {})
    section = dict(manifest[MANIFEST_KEY])
    prompt_shas = dict(section["prompt_shas"])
    prompt_shas[STAGE_RELEVANCE] = "baska-bir-prompt-sha"
    section["prompt_shas"] = prompt_shas
    manifest[MANIFEST_KEY] = section
    run.execution_manifest = manifest
    db_session.add(run)
    db_session.commit()

    fresh_ai = _full_ai(keywords)
    with pytest.raises(StageContextMismatch):
        run_social_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                         ai=fresh_ai, firm_block_sha256=firm_sha,
                         firm_block_text=_firm_text())

    assert fresh_ai.total_calls == 0, (
        "bozulan baglamla HICBIR AI cagrisi YAPILMAMIS olmali (fail-closed, "
        "DAHA OKUMADAN durur)"
    )


# ---------------------------------------------------------------------------
# DUZELTME dogrulamasi — eskiden burada "her zaman basarisiz olur" diye
# raporlanan iki test artik TERSINE CEVRILDI: `ai_runner.run_batch` artik
# `result_key`/`response_id_field` parametreleriyle SOCIAL'in KENDI ilan
# ettigi semasini (duz DIZI + `keyword_id` alani, `{"results":...}` SARMALI
# YOK) dogru okuyor. Asagidaki testler, semaya BIREBIR uyan bir cevabin
# artik ILK denemede basariyla parse edilip asamayi TAMAMLADIGINI kanitlar
# (eskiden ayni girdiyle `SocialStageError` firlardi).
# ---------------------------------------------------------------------------


def test_v4_stage_accepts_schema_conformant_top_level_array(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """Tek kelimelik minimal senaryo: relevance basariyla gecer (SEO
    kalibi degismedi). V4 asamasinda AI, `P.RESPONSE_SCHEMA`nin GERCEKTEN
    istedigi bicimde (duz dizi, `keyword_id` alani) EKSIKSIZ ve GECERLI bir
    cevap doner — artik run_social_stage BASARIYLA TAMAMLANIR, PATLAMAZ."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    kw = keywords[2]  # relevance=0.90 -> V4'e gidecek tek kelime senaryosu icin yeterli

    # `P.RESPONSE_SCHEMA`nin GERCEK BICIMI: duz JSON DIZI, `keyword_id` alani,
    # `{"results": [...]}` SARMALI YOK — tam olarak semanin istedigi gibi.
    # relative_fit BILEREK 50'nin ALTINDA: bu test yalniz V4 asamasinin
    # semaya uygun cevabi basariyla PARSE ETTIGINI izole dogrular — kelime
    # uc kapida elenir, V5 HIC cagrilmaz (bu test V5'i test etmiyor, bkz.
    # asagidaki `test_v5_stage_accepts_schema_conformant_top_level_array`).
    schema_conformant_v4 = json.dumps([
        {"keyword_id": kw.id, "brand_contentability": 90, "attention": 85,
         "scenario": 80, "relative_fit": 40},
    ], ensure_ascii=False)

    # Yalniz kw'i V4'e gonderecek sekilde digerlerini relevance'da eleriz.
    other_ids = {k.id for k in keywords if k.id != kw.id}
    full_relevance = _working_relevance_response({
        **{kw.id: {"relevance": 0.90, "band": None}},
        **{oid: {"relevance": 0.05, "band": None} for oid in other_ids},
    })

    ai = FakeSocialAI({
        STAGE_RELEVANCE: [full_relevance],
        STAGE_V4: [schema_conformant_v4],
    })

    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    result = out["result"]

    assert {r["keyword_id"] for r in result["survivors"]} == {kw.id}
    assert result["eligible"] == []          # RF=40<50 -> uc kapida elendi
    assert len(_stage_rows(db_session, run, STAGE_V4)) == 1
    # ILK denemede basarili — tekrar denemeye HIC gerek kalmadi.
    assert ai.call_counts[STAGE_V4] == 1
    assert ai.call_counts[STAGE_V5] == 0     # elendigi icin V5'e HIC gidilmedi


def test_v5_stage_accepts_schema_conformant_top_level_array(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """Ayni duzeltme V5 icin de gecerlidir (`P.V5_RESPONSE_SCHEMA` da duz
    dizi + `keyword_id`)."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    kw = keywords[2]
    other_ids = {k.id for k in keywords if k.id != kw.id}
    full_relevance = _working_relevance_response({
        **{kw.id: {"relevance": 0.90, "band": None}},
        **{oid: {"relevance": 0.05, "band": None} for oid in other_ids},
    })
    v4_ok = _working_v4_response({kw.id: {"brand_contentability": 90, "attention": 85,
                                          "scenario": 80, "relative_fit": 90}})

    schema_conformant_v5 = json.dumps([
        {"keyword_id": kw.id, "social_intent_type": "CONTENT_NATIVE",
         "intent_confidence": 90, "intent_reason": "gercek sebep"},
    ], ensure_ascii=False)

    ai = FakeSocialAI({
        STAGE_RELEVANCE: [full_relevance],
        STAGE_V4: [v4_ok],
        STAGE_V5: [schema_conformant_v5],
    })

    out = run_social_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, ai=ai,
                           firm_block_sha256=firm_sha, firm_block_text=_firm_text())
    result = out["result"]

    priority_by_id = {r["keyword_id"]: (r["social_priority"], r["social_intent_type"])
                      for r in result["final"]}
    # Tek satirlik evrende NormBounds notr (span=0 -> 0.5); SocialScore ~68.5
    # cikar (yerel olarak dogrulandi) -> CONTENT_NATIVE + 40<=SS<70 -> SECONDARY.
    assert priority_by_id[kw.id] == (PRIORITY_SECONDARY, "CONTENT_NATIVE")
    assert result["pool"][0]["keyword_id"] == kw.id
    assert len(_stage_rows(db_session, run, STAGE_V5)) == 1
    assert ai.call_counts[STAGE_V5] == 1


# ---------------------------------------------------------------------------
# Kilitli parser + NormBounds checkpoint regresyonlari
# ---------------------------------------------------------------------------


def test_relevance_band_conflict_is_canonicalized_before_checkpoint(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """Ham 0.90, `broad` bandinda 0.39'a kirpilir; ham deger checkpoint'e
    relevance diye sizamaz ve V4'e aday yaratamaz."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    conflicting = {
        kw.id: {"relevance": 0.90, "band": "broad"} for kw in keywords}
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(conflicting)]})

    out = run_social_stage(
        db_session, run=run, profile=PROFILE, universe=universe.rows, ai=ai,
        firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert out["result"]["survivors"] == []
    for row in _stage_rows(db_session, run, STAGE_RELEVANCE):
        assert row.payload["relevance"] == 0.39
        assert row.payload["ham"] == 0.90
        assert row.payload["band_kirpildi"] is True
    norm = _stage_rows(db_session, run, STAGE_NORMBOUNDS)
    assert len(norm) == 1
    assert norm[0].scope_type == "run"
    assert norm[0].scope_key == "norm_bounds"
    assert norm[0].payload["state"] == "empty"
    assert norm[0].payload["bounds"] == {}


def test_v4_out_of_range_response_retries_and_only_canonical_values_are_saved(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    invalid = _v4_items(keywords)
    invalid[keywords[1].id] = dict(invalid[keywords[1].id], attention=101)
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        STAGE_V4: [_working_v4_response(invalid),
                   _working_v4_response(_v4_items(keywords))],
        STAGE_V5: [_working_v5_response(_v5_items(keywords))],
    })

    run_social_stage(
        db_session, run=run, profile=PROFILE, universe=universe.rows, ai=ai,
        firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert ai.call_counts[STAGE_V4] == 2
    rows = _stage_rows(db_session, run, STAGE_V4)
    assert len(rows) == 5
    assert all(0 <= row.payload["attention"] <= 100 for row in rows)


def test_v5_invalid_enum_retries_and_only_valid_intent_is_saved(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    invalid = _v5_items(keywords)
    invalid[keywords[2].id] = dict(
        invalid[keywords[2].id], social_intent_type="NOT_A_REAL_INTENT")
    ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        STAGE_V4: [_working_v4_response(_v4_items(keywords))],
        STAGE_V5: [_working_v5_response(invalid),
                   _working_v5_response(_v5_items(keywords))],
    })

    run_social_stage(
        db_session, run=run, profile=PROFILE, universe=universe.rows, ai=ai,
        firm_block_sha256=firm_sha, firm_block_text=_firm_text())

    assert ai.call_counts[STAGE_V5] == 2
    rows = _stage_rows(db_session, run, STAGE_V5)
    assert len(rows) == 4
    assert {row.payload["social_intent_type"] for row in rows} <= set(P.INTENT_CLASSES)


def test_invalid_v4_never_checkpoints_and_resume_uses_frozen_normbounds(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_social_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    invalid = _v4_items(keywords)
    invalid[keywords[1].id] = dict(invalid[keywords[1].id], relative_fit=-1)
    first_ai = FakeSocialAI({
        STAGE_RELEVANCE: [_working_relevance_response(_relevance_items(keywords))],
        STAGE_V4: [_working_v4_response(invalid), _working_v4_response(invalid)],
    })
    with pytest.raises(SocialStageError, match="yapisal dogrulamadan gecmedi"):
        run_social_stage(
            db_session, run=run, profile=PROFILE, universe=universe.rows,
            ai=first_ai, firm_block_sha256=firm_sha,
            firm_block_text=_firm_text())

    assert _stage_rows(db_session, run, STAGE_V4) == []
    assert len(_stage_rows(db_session, run, STAGE_NORMBOUNDS)) == 1

    resume_ai = FakeSocialAI({
        STAGE_V4: [_working_v4_response(_v4_items(keywords))],
        STAGE_V5: [_working_v5_response(_v5_items(keywords))],
    })
    run_social_stage(
        db_session, run=run, profile=PROFILE, universe=universe.rows,
        ai=resume_ai, firm_block_sha256=firm_sha,
        firm_block_text=_firm_text())
    assert resume_ai.call_counts[STAGE_RELEVANCE] == 0
    assert resume_ai.call_counts[STAGE_V4] == 1
    assert len(_stage_rows(db_session, run, STAGE_NORMBOUNDS)) == 1
