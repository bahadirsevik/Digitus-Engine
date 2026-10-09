"""ADS Niche kosucusu entegrasyon testleri (`app/core/engine/ads/runner.py`).

QA gorevi: Motor v3 Faz 3 = ADS Niche motoru. Butun AI etkilesimi SAHTE bir
istemciyle kurulur (`FakeAdsAI` / `QueueAdsAI`); GERCEK saglayiciya HICBIR
cagri yapilmaz. Akis: funnel (Rel + huni + brand_type, batch 10) -> intent
(Intent V4 ham puani, batch 10; `apply_band` ile huni bandina KIRPILIR) ->
`run_niche_engine`. Girdiler DONMUS evrenden (`freeze_universe_snapshot`)
ve disaridan verilen `family_by_id`den gelir.

Faz 2 (`tests/integration/test_engine_family_runner.py`) ve K17
(`tests/unit/test_engine_ai_runner.py`) ile AYNI kalip kullanilir:
`for_stage(stage, model=..., thinking_level=...)` StageScopedAIService
deseni + sirali hazir cevap kuyrugu, bos kuyrukta AssertionError (gercek
saglayiciya kaymayi engeller).
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

import pytest

from app.core.engine import context as CTX
from app.core.engine.ads.runner import (
    ADS_MODEL,
    ADS_STAGES,
    SCOPE_KEYWORD,
    STAGE_FUNNEL,
    STAGE_INTENT,
    ads_models,
    ads_prompt_shas,
    run_ads_stage,
)
from app.core.engine.ads import engine as ENG
from app.core.engine.ads import formula as F
from app.core.engine.ads import prompts as P
from app.core.engine.ads.runner import validated_family_map
from app.core.engine.ai_runner import AiStageError
from app.core.engine.context import EngineInputError
from app.core.engine.family.rules import UNMATCHED
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    MANIFEST_KEY,
    StageContext,
    StageContextMismatch,
    manifest_section,
    seal_manifest,
    verify_stage_context,
)
from app.database.models import EngineStageResult

PROFILE = {
    "sector": "yatirim",
    "brand_name": "Digitus Yatirim",
    "brand_summary": "Yatirim araclari ve fon danismanligi sunan marka",
    "products": ["hisse senedi", "yatirim fonu"],
}


def _firm_sha(profile: Dict[str, Any] = PROFILE) -> str:
    return CTX.firm_block_sha256(CTX.firm_block(profile))


def _seal_ads_manifest(run, *, firm_sha: str = None) -> None:
    seal_manifest(
        run,
        firm_block_sha256=firm_sha or _firm_sha(),
        algorithm_versions={"ads": "nihai_niche_v1"},
        models=ads_models(),
        prompt_shas=ads_prompt_shas(),
        location_policy=_loc_snap({}),
    )


class _ScopedFakeAdsAI:
    def __init__(self, parent: "FakeAdsAI", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def complete_json(self, prompt: str, max_tokens: int = None,
                       response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond(self._stage, prompt)


class FakeAdsAI:
    """`complete_json` cagrilarini SAYAN + sirayla hazir cevap dondüren
    sahte istemci. GERCEK saglayiciya ASLA gitmez.

    Kuyruktaki oge `BaseException` ORNEGIYSE oldugu gibi firlatilir
    (saglayici/transport hatasi simulasyonu); aksi halde HAM STRING JSON
    cevap olarak dondurulur.
    """

    def __init__(self, stage_responses: Dict[str, List[Any]]) -> None:
        self._queues: Dict[str, List[Any]] = {
            stage: list(payloads) for stage, payloads in stage_responses.items()
        }
        self.calls: List[tuple] = []
        self.call_counts: Counter = Counter()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "_ScopedFakeAdsAI":
        return _ScopedFakeAdsAI(self, stage)

    def _respond(self, stage: str, prompt: str) -> str:
        self.calls.append((stage, prompt))
        self.call_counts[stage] += 1
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"FakeAdsAI: '{stage}' icin kuyrukta hazir cevap kalmadi "
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


# ---------------------------------------------------------------------------
# ortak kurulum: donmus evren (freeze_universe_snapshot)
# ---------------------------------------------------------------------------

KEYWORD_SPECS = [
    # text, volume, competition_score, trend_3m(%)
    ("kelime bir", 1000, 0.30, 20.0),
    ("kelime iki", 500, 0.50, 0.0),
    ("kelime uc", 200, 0.20, -10.0),
    ("kelime dort", 50, 0.80, 100.0),
]


def _make_frozen_universe(db_session, make_workspace, make_scoring_run,
                          make_keyword, specs=KEYWORD_SPECS):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    keywords = []
    for text, volume, competition, trend_3m in specs:
        kw = make_keyword(text, brand_profile_id=workspace.id,
                          monthly_volume=volume, competition_score=competition,
                          trend_3m=trend_3m)
        keywords.append(kw)
    db_session.commit()
    universe = CTX.freeze_universe_snapshot(db_session, run)
    db_session.commit()
    return workspace, run, keywords, universe


def _funnel_response(keywords) -> Dict[str, Any]:
    funnel_map = {
        keywords[0].id: ("transactional", "yok", 0.90),
        keywords[1].id: ("commercial", "yok", 0.60),
        keywords[2].id: ("informational", "yok", 0.30),
        keywords[3].id: ("transactional", "kendi", 0.95),
    }
    return {"results": [
        {"id": kid, "funnel": funnel, "brand_type": brand, "relevance": rel}
        for kid, (funnel, brand, rel) in funnel_map.items()
    ]}


def _intent_response(keywords) -> Dict[str, Any]:
    # kw4 (dorduncu) BILEREK commercial bandinin ustunde (0.95): apply_band
    # 0.7999'a kirpmali. Diger uc kelime bant ICINDE, kirpilmaz.
    intent_map = {
        keywords[0].id: ("transactional", 0.90),
        keywords[1].id: ("commercial", 0.50),
        keywords[2].id: ("informational", 0.20),
        keywords[3].id: ("commercial", 0.95),
    }
    return {"results": [
        {"id": kid, "funnel": funnel, "intent": intent}
        for kid, (funnel, intent) in intent_map.items()
    ]}


def _family_by_id(keywords) -> Dict[int, str]:
    return {
        keywords[0].id: "fam_a",
        keywords[1].id: "fam_a",
        keywords[2].id: "fam_b",
        keywords[3].id: "fam_b",
    }


def _expected_engine_rows(keywords, universe) -> List[Dict[str, Any]]:
    """Runner'in URETMESI GEREKEN motor girdisini bagimsiz olarak kurar
    (apply_band kirpmasi DAHIL) — `run_niche_engine`'e ayri ayri verilip
    runner ciktisiyla karsilastirilir."""
    funnel = _funnel_response(keywords)["results"]
    intent = _intent_response(keywords)["results"]
    funnel_by_id = {item["id"]: item for item in funnel}
    intent_by_id = {item["id"]: item for item in intent}
    family_by_id = _family_by_id(keywords)

    by_id = {row.keyword_id: row for row in universe.rows}
    rows = []
    flags: Dict[str, int] = {}
    for kid in by_id:
        urow = by_id[kid]
        intent_value, _clamped = P.apply_band(intent_by_id[kid]["funnel"],
                                              intent_by_id[kid]["intent"])
        rows.append({
            "keyword_id": kid,
            "keyword_text": urow.keyword_text,
            "volume": urow.volume,
            "R": F.competition_to_r(urow.competition, flags),
            "T": F.trend_to_t(urow.trend_3m, flags),
            "Rel": funnel_by_id[kid]["relevance"],
            "Intent": intent_value,
            "family": family_by_id[kid],
        })
    return rows


def _ai_for(keywords) -> FakeAdsAI:
    return FakeAdsAI({
        STAGE_FUNNEL: [_ok(_funnel_response(keywords))],
        STAGE_INTENT: [_ok(_intent_response(keywords))],
    })


# ---------------------------------------------------------------------------
# muhur sozlesmesi — ads_prompt_shas()/ads_models() runner'in bekledigi
# baglamla uyusur
# ---------------------------------------------------------------------------


def test_ads_manifest_sealed_with_ads_prompt_shas_and_models_matches_runner_context(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    _seal_ads_manifest(run)
    db_session.commit()

    section = manifest_section(run)
    assert section["models"] == ads_models()
    assert section["prompt_shas"] == ads_prompt_shas()
    assert section["firm_block_sha256"] == _firm_sha()

    for stage in ADS_STAGES:
        ctx = StageContext(model=ADS_MODEL, prompt_sha=ads_prompt_shas()[stage],
                           firm_block_sha256=_firm_sha())
        verify_stage_context(run, stage, ctx)  # patlamamali


# ---------------------------------------------------------------------------
# Tam akis: funnel -> intent -> Niche motoru, donmus evrenle
# ---------------------------------------------------------------------------


def test_run_ads_stage_full_flow_produces_pool_consistent_with_order_pool(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    result = run_ads_stage(db_session, run=run, profile=PROFILE,
                           rows=universe.rows, family_by_id=_family_by_id(keywords),
                           ai=ai, firm_block_sha256=firm_sha)

    expected_rows = _expected_engine_rows(keywords, universe)
    expected_pool, expected_checks = ENG.run_niche_engine(expected_rows)

    got_ids = [row["keyword_id"] for row in result["pool"]]
    expected_ids = [row["keyword_id"] for row in expected_pool]
    assert got_ids == expected_ids, "havuz sirasi bagimsiz motor kosusuyla uyusmuyor"

    for got, want in zip(result["pool"], expected_pool):
        assert got["Selection"] == pytest.approx(want["Selection"], abs=1e-9)
        assert got["Core"] == pytest.approx(want["Core"], abs=1e-9)

    assert result["checks"]["mfv_acik"] is True
    assert result["checks"]["familyrelq_acik"] is True
    assert len(result["pool"]) == expected_checks["kalan_satir"]


# ---------------------------------------------------------------------------
# Intent bandi uygulaniyor: band disi intent motora KIRPILMIS girer
# ---------------------------------------------------------------------------


def test_intent_band_clamp_is_applied_before_entering_the_engine(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    result = run_ads_stage(db_session, run=run, profile=PROFILE,
                           rows=universe.rows, family_by_id=_family_by_id(keywords),
                           ai=ai, firm_block_sha256=firm_sha)

    kw4_id = keywords[3].id
    # Ham AI cevabi (STAGE_INTENT sonucu) DEGISMEDEN saklanir.
    assert result["intent"][kw4_id]["intent"] == 0.95
    assert result["intent"][kw4_id]["funnel"] == "commercial"

    # Ama motora giren Intent, commercial bandinin clamp_high'ina (0.7999)
    # kirpilmis olmali — bant disi deger dogrudan gecmemeli.
    pool_row = next(r for r in result["pool"] if r["keyword_id"] == kw4_id)
    assert pool_row["Intent"] == pytest.approx(0.7999)
    assert pool_row["Intent"] != pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Resume: ikinci cagri AI'yi TEKRAR cagirmaz, sonuc birebir aynidir
# ---------------------------------------------------------------------------


def test_run_ads_stage_resume_does_not_call_ai_again_and_result_is_identical(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    result1 = run_ads_stage(db_session, run=run, profile=PROFILE,
                            rows=universe.rows, family_by_id=_family_by_id(keywords),
                            ai=ai, firm_block_sha256=firm_sha)
    calls_after_first = ai.total_calls
    assert calls_after_first > 0

    result2 = run_ads_stage(db_session, run=run, profile=PROFILE,
                            rows=universe.rows, family_by_id=_family_by_id(keywords),
                            ai=ai, firm_block_sha256=firm_sha)

    assert ai.total_calls == calls_after_first, (
        "ikinci run_ads_stage cagrisi AI'yi TEKRAR cagirdi — resume "
        "engine_stage_results'tan okumuyor"
    )
    assert result1 == result2

    rows_in_db = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .all()
    )
    assert rows_in_db, "hicbir asama satiri yazilmadi"
    assert {r.scope_type for r in rows_in_db} == {SCOPE_KEYWORD}
    assert {r.stage for r in rows_in_db} == {STAGE_FUNNEL, STAGE_INTENT}


# ---------------------------------------------------------------------------
# Baglam uyusmazligi: manifest degisirse resume SESSIZCE tekrar kullanilmaz
# ---------------------------------------------------------------------------


def test_run_ads_stage_resume_with_manifest_drift_raises_stage_context_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                 family_by_id=_family_by_id(keywords), ai=ai,
                 firm_block_sha256=firm_sha)
    db_session.commit()

    # Run'in GERCEK muhurlu baglamini dogrudan bozuyoruz (manifest drift
    # simulasyonu) — seal_manifest'in kendisi FARKLI degerle YENIDEN
    # muhurlemeye izin vermez, o yuzden ORM nesnesini dogrudan degistiriyoruz.
    manifest = dict(run.execution_manifest or {})
    section = dict(manifest[MANIFEST_KEY])
    prompt_shas = dict(section["prompt_shas"])
    prompt_shas[STAGE_FUNNEL] = "baska-bir-prompt-sha"
    section["prompt_shas"] = prompt_shas
    manifest[MANIFEST_KEY] = section
    run.execution_manifest = manifest
    db_session.add(run)
    db_session.commit()

    fresh_ai = _ai_for(keywords)
    with pytest.raises(StageContextMismatch):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=_family_by_id(keywords), ai=fresh_ai,
                     firm_block_sha256=firm_sha)

    # Sessizce yeniden kullanim YOK: bozulan baglamla hicbir AI cagrisi
    # YAPILMAMIS olmali (fail-closed, DAHA OKUMADAN durur).
    assert fresh_ai.total_calls == 0


# ---------------------------------------------------------------------------
# K17: eksik ID hedefli tekrardan sonra da eksikse hata + o asamadan
# HICBIR satir yazilmaz (yarim checkpoint yok)
# ---------------------------------------------------------------------------


def test_missing_id_after_targeted_retry_raises_and_writes_no_funnel_rows(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    two_keyword_specs = [
        ("tek kelime bir", 300, 0.40, 10.0),
        ("tek kelime iki", 150, 0.60, 5.0),
    ]
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        specs=two_keyword_specs)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    kid_a, kid_b = keywords[0].id, keywords[1].id
    # Ilk cevap yalniz kid_a'yi tasir; hedefli tekrar de kid_b'yi getirmiyor.
    first = _ok({"results": [
        {"id": kid_a, "funnel": "transactional", "brand_type": "yok",
         "relevance": 0.8},
    ]})
    retry = _ok({"results": []})
    ai = FakeAdsAI({STAGE_FUNNEL: [first, retry]})

    with pytest.raises(AiStageError, match="checkpoint olusturulmaz"):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id={kid_a: "fam", kid_b: "fam"},
                     ai=ai, firm_block_sha256=firm_sha)

    # STAGE_INTENT hic tetiklenmemis olmali (funnel asamasinda durdu).
    assert ai.call_counts[STAGE_INTENT] == 0

    funnel_rows = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_FUNNEL)
        .all()
    )
    assert funnel_rows == [], (
        "funnel asamasi basarisiz oldugu halde satir yazilmis — yarim checkpoint"
    )


# ---------------------------------------------------------------------------
# Codex incelemesi: aile dogrulamasi (`validated_family_map`) AI cagrisindan
# ve stage yazimindan ONCE calisir. Butun hata vakalarinda: AI cagri sayisi
# SIFIR ve engine_stage_results satir sayisi SIFIR (bozuk aile girdisiyle
# para harcanmaz, yarim checkpoint olusmaz).
# ---------------------------------------------------------------------------


def _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai: FakeAdsAI) -> None:
    assert ai.total_calls == 0, (
        "aile dogrulamasi patladigi halde AI cagrildi — kapı AI'dan ONCE degil"
    )
    rows_in_db = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .all()
    )
    assert rows_in_db == [], (
        "aile dogrulamasi patladigi halde engine_stage_results satiri yazilmis "
        "— yarim checkpoint"
    )


def test_validated_family_map_accepts_string_keys_from_json_and_feeds_correct_family(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """JSON'dan gelen aile sonucu string ID tasir (`{"101": "fam_a", ...}`) —
    `validated_family_map` bunlari int'e normalize eder; akis normal koşar ve
    motora DOGRU aile girer (havuzdaki her satirin 'family' alani kontrol
    edilir, yalniz akisin patlamadigi degil)."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    expected_family = _family_by_id(keywords)
    string_family_by_id = {str(kid): fam for kid, fam in expected_family.items()}

    ai = _ai_for(keywords)
    result = run_ads_stage(db_session, run=run, profile=PROFILE,
                           rows=universe.rows, family_by_id=string_family_by_id,
                           ai=ai, firm_block_sha256=firm_sha)

    assert ai.total_calls > 0  # bu senaryoda akis GERCEKTEN kosmali
    families_in_pool = {row["keyword_id"]: row["family"] for row in result["pool"]}
    assert families_in_pool == expected_family


def test_validated_family_map_missing_keyword_id_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    incomplete = _family_by_id(keywords)
    del incomplete[keywords[0].id]          # tam olarak 1 ID eksik

    ai = _ai_for(keywords)                  # kuyruklar DOLU ama HIC TUKETILMEMELI
    with pytest.raises(EngineInputError, match=r"1 eksik") as excinfo:
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=incomplete, ai=ai, firm_block_sha256=firm_sha)
    assert "0 fazla" in str(excinfo.value)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


def test_validated_family_map_extra_keyword_id_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    with_extra = _family_by_id(keywords)
    with_extra[999_999_999] = "hayali_aile"  # evrende OLMAYAN bir ID

    ai = _ai_for(keywords)
    with pytest.raises(EngineInputError, match=r"1 fazla") as excinfo:
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=with_extra, ai=ai, firm_block_sha256=firm_sha)
    assert "0 eksik" in str(excinfo.value)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


def test_validated_family_map_unmatched_sentinel_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """Kapanista `UNMATCHED` kalmis bir kelime motor girdisi OLAMAZ —
    runner'a girmeden ONCE Faz 2'nin `single:<id>` kapanisini tamamlamis
    olmasi gerekir; `UNMATCHED` sizarsa fail-closed durur."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    with_unmatched = _family_by_id(keywords)
    with_unmatched[keywords[2].id] = UNMATCHED

    ai = _ai_for(keywords)
    with pytest.raises(EngineInputError, match=r"gecersiz aile"):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=with_unmatched, ai=ai, firm_block_sha256=firm_sha)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


@pytest.mark.parametrize("bad_value", [None, "", "   "],
                         ids=["none", "empty_string", "whitespace_only"])
def test_validated_family_map_empty_or_none_family_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword, bad_value,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    bad_family = _family_by_id(keywords)
    bad_family[keywords[1].id] = bad_value

    ai = _ai_for(keywords)
    with pytest.raises(EngineInputError, match=r"gecersiz aile"):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=bad_family, ai=ai, firm_block_sha256=firm_sha)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


def test_validated_family_map_non_numeric_key_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    bad_key_family = _family_by_id(keywords)
    bad_key_family["not-a-number"] = "hayali_aile"

    ai = _ai_for(keywords)
    with pytest.raises(EngineInputError, match=r"sayisal olmayan keyword id"):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=bad_key_family, ai=ai, firm_block_sha256=firm_sha)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


def test_validated_family_map_duplicate_normalized_key_raises_and_spends_nothing(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    """Ayni keyword_id'ye int VE string anahtarla CIFT deger verilmesi —
    normalize sonrasi carpisan anahtar `EngineInputError` firlatmali."""
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_ads_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    duplicated = _family_by_id(keywords)
    dup_target = keywords[0].id
    duplicated[str(dup_target)] = duplicated[dup_target]  # AYNI id'ye ikinci (string) anahtar

    ai = _ai_for(keywords)
    with pytest.raises(EngineInputError, match=r"tekrarli keyword id"):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=universe.rows,
                     family_by_id=duplicated, ai=ai, firm_block_sha256=firm_sha)

    _assert_no_ai_calls_and_no_stage_rows(db_session, run, ai)


# ---------------------------------------------------------------------------
# `validated_family_map` birim seviyesinde dogrudan (DB'siz) — pozitif yol
# ---------------------------------------------------------------------------


def test_validated_family_map_unit_normalizes_string_keys_to_int_and_str_values():
    class _Row:
        def __init__(self, keyword_id):
            self.keyword_id = keyword_id

    rows = [_Row(1), _Row(2)]
    result = validated_family_map({"1": "fam_a", 2: "fam_b"}, rows)
    assert result == {1: "fam_a", 2: "fam_b"}
    assert all(isinstance(v, str) for v in result.values())
