# -*- coding: utf-8 -*-
"""SEO katı-2 kosucusu entegrasyon testleri (`app/core/engine/seo/runner.py`).

QA gorevi: Motor v3 Faz 4 = SEO katı-2. Butun AI etkilesimi SAHTE bir
istemciyle kurulur (`FakeSeoAI`); GERCEK saglayiciya HICBIR cagri yapilmaz
(UCRETLI CAGRI YOK). Faz 2/3 ile AYNI kalip: `for_stage(stage, model=...,
thinking_level=...)` StageScopedAIService deseni + sirali hazir cevap
kuyrugu, bos kuyrukta AssertionError (gercek saglayiciya kaymayi engeller).

Akis (SIRA BAGLAYICIDIR — runner.py docstring'i):
    rel -> bp (yalniz Rel>=0,50) -> subintent (aile basina TEK cagri)
        -> authority (yalniz Rel>=0,85 ve 0,70<=BP<0,80 penceresi)
        -> 1. GECIS (adaylar) -> URL grubu (>=2 adayi olan aile basina TEK cagri)
        -> KAPI -> 2. GECIS (run_two_pass, gruplarla gercek secim)

Sabit kurgu (5 keyword, 2 aile):
    famA: k1(s1), k2(s2), k3(s3, YALNIZ authority istisnasiyla eligible)
    famB: k4(t1, eligible), k5(t1, rel bant-disi -> kirpilir, BP evreni DISINDA)
  -> famA 3 adayli (url grubu TETIKLENIR), famB 1 adayli (TETIKLENMEZ)
  -> authority penceresi YALNIZ k3
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Optional

import pytest

from app.core.engine import context as CTX
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    MANIFEST_KEY,
    StageContext,
    StageContextMismatch,
    manifest_section,
    seal_manifest,
    verify_stage_context,
)
from app.core.engine.seo import prompts as P
from app.core.engine.seo import runner as R
from app.core.engine.seo.runner import (
    SCOPE_BY_STAGE,
    SCOPE_FAMILY,
    SCOPE_KEYWORD,
    SCOPE_URL_GROUP,
    SEO_MODEL,
    SEO_STAGES,
    STAGE_AUTHORITY,
    STAGE_BP,
    STAGE_REL,
    STAGE_SUBINTENT,
    STAGE_URLGROUP,
    SeoStageError,
    run_seo_stage,
    seo_models,
    seo_prompt_shas,
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


def _seal_seo_manifest(run, *, firm_sha: str = None) -> None:
    seal_manifest(
        run,
        firm_block_sha256=firm_sha or _firm_sha(),
        algorithm_versions={"seo": "seo_v31_kati2_v2"},
        models=seo_models(),
        prompt_shas=seo_prompt_shas(),
        location_policy=_loc_snap({}),
    )


class _ScopedFakeSeoAI:
    def __init__(self, parent: "FakeSeoAI", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def complete_json(self, prompt: str, max_tokens: int = None,
                      response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond(self._stage, prompt)


class FakeSeoAI:
    """`complete_json` cagrilarini SAYAN + sirayla hazir cevap dondüren sahte
    istemci. GERCEK saglayiciya ASLA gitmez.

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
                 thinking_level: str = None) -> "_ScopedFakeSeoAI":
        return _ScopedFakeSeoAI(self, stage)

    def _respond(self, stage: str, prompt: str) -> str:
        self.calls.append((stage, prompt))
        self.call_counts[stage] += 1
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"FakeSeoAI: '{stage}' icin kuyrukta hazir cevap kalmadi "
                f"(bu {self.call_counts[stage]}. cagri) — GERCEK saglayiciya "
                "gidilmeye CALISILIYOR olabilir, bu test bunu engeller.")
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
# ortak kurulum: donmus evren (freeze_universe_snapshot), 2 aile, 5 keyword
# ---------------------------------------------------------------------------

KEYWORD_SPECS = [
    # text, volume, competition, trend_3m, trend_12m
    ("kelime bir", 500, 0.30, 0.0, 0.0),   # famA / s1
    ("kelime iki", 400, 0.35, 0.0, 0.0),   # famA / s2
    ("kelime uc", 300, 0.40, 0.0, 0.0),    # famA / s3 (yalniz authority ile eligible)
    ("kelime dort", 200, 0.45, 0.0, 0.0),  # famB / t1 (eligible)
    ("kelime bes", 150, 0.50, 0.0, 0.0),   # famB / t1 (rel kirpilir, BP disi)
]


def _make_frozen_universe(db_session, make_workspace, make_scoring_run, make_keyword):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    keywords = []
    for text, volume, competition, t3, t12 in KEYWORD_SPECS:
        kw = make_keyword(text, brand_profile_id=workspace.id,
                          monthly_volume=volume, competition_score=competition,
                          trend_3m=t3, trend_12m=t12)
        keywords.append(kw)
    db_session.commit()
    universe = CTX.freeze_universe_snapshot(db_session, run)
    db_session.commit()
    return workspace, run, keywords, universe


def _family_by_id(keywords) -> Dict[int, str]:
    k1, k2, k3, k4, k5 = keywords
    return {k1.id: "famA", k2.id: "famA", k3.id: "famA",
            k4.id: "famB", k5.id: "famB"}


def _families() -> List[Dict[str, Any]]:
    return [{"family_id": "famA", "family_name": "Aile A", "core_need": "ihtiyac_a",
             "solution_type": "cozum_a"},
            {"family_id": "famB", "family_name": "Aile B", "core_need": "ihtiyac_b",
             "solution_type": "cozum_b"}]


# ---------------------------------------------------------------------------
# sahte AI cevaplari — sabit kurguyu besler
# ---------------------------------------------------------------------------

def _rel_response(keywords) -> Dict[str, Any]:
    k1, k2, k3, k4, k5 = keywords
    return {"results": [
        {"id": k1.id, "band": "strong", "relevance": 0.90},
        {"id": k2.id, "band": "strong", "relevance": 0.90},
        {"id": k3.id, "band": "strong", "relevance": 0.87},
        {"id": k4.id, "band": "strong", "relevance": 0.90},
        # k5: BANT DISI deger — "broad" ust siniri 0,39, AI 0,50 donuyor;
        # KIRPILMIS deger (0,39) DB'ye YAZILMALI, ham (0,50) DEGIL. Kirpilirsa
        # k5 BP evreninin (Rel>=0,50) DISINDA kalir.
        {"id": k5.id, "band": "broad", "relevance": 0.50},
    ]}


def _bp_response(keywords) -> Dict[str, Any]:
    k1, k2, k3, k4 = keywords[0], keywords[1], keywords[2], keywords[3]
    return {"results": [
        {"id": k1.id, "band": "very_close", "business_proximity": 0.85},
        {"id": k2.id, "band": "very_close", "business_proximity": 0.85},
        # k3: authority penceresi icin BILEREK 0,70<=bp<0,80
        {"id": k3.id, "band": "support", "business_proximity": 0.72},
        {"id": k4.id, "band": "very_close", "business_proximity": 0.85},
    ]}


def _subintent_response_famA(keywords) -> Dict[str, Any]:
    k1, k2, k3 = keywords[0], keywords[1], keywords[2]
    return {"results": [
        {"id": k1.id, "broad_intent": "informational", "subintent_id": "s1",
         "subintent_label": "S Bir"},
        {"id": k2.id, "broad_intent": "informational", "subintent_id": "s2",
         "subintent_label": "S Iki"},
        {"id": k3.id, "broad_intent": "informational", "subintent_id": "s3",
         "subintent_label": "S Uc"},
    ]}


def _subintent_response_famB(keywords) -> Dict[str, Any]:
    k4, k5 = keywords[3], keywords[4]
    return {"results": [
        {"id": k4.id, "broad_intent": "informational", "subintent_id": "t1",
         "subintent_label": "T Bir"},
        {"id": k5.id, "broad_intent": "informational", "subintent_id": "t1",
         "subintent_label": "T Bir"},
    ]}


def _authority_response(keywords) -> Dict[str, Any]:
    k3 = keywords[2]
    return {"results": [{"id": k3.id, "authority": "high"}]}


def _urlgroup_response_famA(keywords) -> Dict[str, Any]:
    k1, k2, k3 = keywords[0], keywords[1], keywords[2]
    return {"groups": [
        {"group_id": "g1", "ids": [k1.id, k2.id]},
        {"group_id": "g2", "ids": [k3.id]},
    ]}


def _ai_for(keywords) -> FakeSeoAI:
    return FakeSeoAI({
        STAGE_REL: [_ok(_rel_response(keywords))],
        STAGE_BP: [_ok(_bp_response(keywords))],
        STAGE_SUBINTENT: [_ok(_subintent_response_famA(keywords)),
                          _ok(_subintent_response_famB(keywords))],
        STAGE_AUTHORITY: [_ok(_authority_response(keywords))],
        STAGE_URLGROUP: [_ok(_urlgroup_response_famA(keywords))],
    })


def _assert_second_pass_never_started(monkeypatch) -> list:
    """`run_two_pass` cagrilarini sayan spy kurar; cagrilan run_seo_stage'in
    2. GECISE hic girmedigini kanitlamak icin kullanilir."""
    calls: list = []
    real = R.run_two_pass

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(R, "run_two_pass", spy)
    return calls


# ---------------------------------------------------------------------------
# muhur sozlesmesi — seo_prompt_shas()/seo_models() runner'in bekledigi
# baglamla uyusur
# ---------------------------------------------------------------------------


def test_seo_manifest_sealed_with_seo_prompt_shas_and_models_matches_runner_context(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    _seal_seo_manifest(run)
    db_session.commit()

    section = manifest_section(run)
    assert section["models"] == seo_models()
    assert section["prompt_shas"] == seo_prompt_shas()
    assert section["firm_block_sha256"] == _firm_sha()

    for stage in SEO_STAGES:
        ctx = StageContext(model=SEO_MODEL, prompt_sha=seo_prompt_shas()[stage],
                           firm_block_sha256=_firm_sha())
        verify_stage_context(run, stage, ctx)  # patlamamali


# ---------------------------------------------------------------------------
# Tam akis: sira, kapılar, bant kirpma, URL grubu esigi
# ---------------------------------------------------------------------------


def test_run_seo_stage_full_flow_order_gates_and_band_clamp(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    result = run_seo_stage(db_session, run=run, profile=PROFILE,
                           universe=universe.rows, family_by_id=_family_by_id(keywords),
                           families=_families(), ai=ai, firm_block_sha256=firm_sha)

    # ── sira/cagri sayilari ────────────────────────────────────────────
    assert ai.call_counts[STAGE_REL] == 1
    assert ai.call_counts[STAGE_BP] == 1
    assert ai.call_counts[STAGE_SUBINTENT] == 2       # aile basina TEK cagri (famA, famB)
    assert ai.call_counts[STAGE_AUTHORITY] == 1        # yalniz pencere (k3)
    assert ai.call_counts[STAGE_URLGROUP] == 1         # yalniz famA (>=2 aday); famB DEGIL

    k1, k2, k3, k4, k5 = keywords

    # ── BP cagrisina giden ID kumesi: SADECE (kirpilmis) Rel>=0,50 ──────
    bp_scope_keys = {
        row.scope_key for row in db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_BP).all()
    }
    assert bp_scope_keys == {str(k1.id), str(k2.id), str(k3.id), str(k4.id)}
    assert str(k5.id) not in bp_scope_keys, (
        "k5 relevance'i bant-disi (0,50) geldi; KIRPILMAMIS olsaydi BP "
        "evrenine (Rel>=0,50) yanlislikla girerdi")

    # ── authority cagrisina giden ID kumesi: SADECE pencere (k3) ────────
    auth_scope_keys = {
        row.scope_key for row in db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_AUTHORITY).all()
    }
    assert auth_scope_keys == {str(k3.id)}

    # ── url grubu cagrisina giden AILE kumesi: SADECE famA ───────────────
    url_scope_keys = {
        row.scope_key for row in db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_URLGROUP).all()
    }
    assert url_scope_keys == {"famA"}

    # ── bant kirpmasi DB'de: k5 icin HAM (0,50) DEGIL, KIRPILMIS (0,39) ──
    rel_row = (db_session.query(EngineStageResult)
              .filter(EngineStageResult.scoring_run_id == run.id,
                      EngineStageResult.stage == STAGE_REL,
                      EngineStageResult.scope_key == str(k5.id)).one())
    assert rel_row.payload["relevance"] == pytest.approx(0.39)
    assert rel_row.payload["ham"] == pytest.approx(0.50)
    assert rel_row.payload["band_kirpildi"] is True

    # ── scope_type'lar dogru (keyword / family / url_group) ─────────────
    for row in db_session.query(EngineStageResult).filter(
            EngineStageResult.scoring_run_id == run.id).all():
        assert row.scope_type == SCOPE_BY_STAGE[row.stage], (
            f"{row.stage}/{row.scope_key}: scope_type={row.scope_type!r} "
            f"beklenen={SCOPE_BY_STAGE[row.stage]!r}")

    # ── URL grubu esik: famA 2 gruba ayrildi (k1,k2 -> g1 ; k3 -> g2),
    #    famB icin hic EK-F verisi yok -> varsayilan KENDI grubu ───────────
    cg = result["selection"]["cluster_groups"]
    cl_k1 = "famA|informational|s1"
    cl_k2 = "famA|informational|s2"
    cl_k3 = "famA|informational|s3"
    cl_k4 = "famB|informational|t1"
    assert cg[cl_k1] == "famA|g1"
    assert cg[cl_k2] == "famA|g1"
    assert cg[cl_k1] == cg[cl_k2]
    assert cg[cl_k3] == "famA|g2"
    assert cg[cl_k3] != cg[cl_k1]
    assert cg[cl_k4] == f"famB|__self__|{cl_k4}"       # varsayilan self-grup


# ---------------------------------------------------------------------------
# Resume: ikinci cagri AI'yi TEKRAR cagirmaz, sonuc birebir aynidir
# ---------------------------------------------------------------------------


def test_run_seo_stage_resume_does_not_call_ai_again_and_result_is_identical(
    db_session, make_workspace, make_scoring_run, make_keyword,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    result1 = run_seo_stage(db_session, run=run, profile=PROFILE,
                            universe=universe.rows, family_by_id=_family_by_id(keywords),
                            families=_families(), ai=ai, firm_block_sha256=firm_sha)
    calls_after_first = ai.total_calls
    assert calls_after_first > 0

    result2 = run_seo_stage(db_session, run=run, profile=PROFILE,
                            universe=universe.rows, family_by_id=_family_by_id(keywords),
                            families=_families(), ai=ai, firm_block_sha256=firm_sha)

    assert ai.total_calls == calls_after_first, (
        "ikinci run_seo_stage cagrisi AI'yi TEKRAR cagirdi — resume "
        "engine_stage_results'tan okumuyor")
    assert result1 == result2

    rows_in_db = (db_session.query(EngineStageResult)
                 .filter(EngineStageResult.scoring_run_id == run.id).all())
    assert rows_in_db, "hicbir asama satiri yazilmadi"
    assert {r.stage for r in rows_in_db} == set(SEO_STAGES)
    assert {r.scope_type for r in rows_in_db} == {SCOPE_KEYWORD, SCOPE_FAMILY,
                                                   SCOPE_URL_GROUP}


# ---------------------------------------------------------------------------
# Muhurlu baglam: authority / urlgroup icin manifest kaydigi resume'u durdurur
# ---------------------------------------------------------------------------


def _drift_manifest_prompt_sha(db_session, run, stage: str) -> None:
    manifest = dict(run.execution_manifest or {})
    section = dict(manifest[MANIFEST_KEY])
    prompt_shas = dict(section["prompt_shas"])
    prompt_shas[stage] = "baska-bir-prompt-sha"
    section["prompt_shas"] = prompt_shas
    manifest[MANIFEST_KEY] = section
    run.execution_manifest = manifest
    db_session.add(run)
    db_session.commit()


@pytest.mark.parametrize("drifted_stage", [STAGE_AUTHORITY, STAGE_URLGROUP])
def test_run_seo_stage_resume_with_manifest_drift_raises_stage_context_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword, drifted_stage,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    ai = _ai_for(keywords)
    run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                 family_by_id=_family_by_id(keywords), families=_families(),
                 ai=ai, firm_block_sha256=firm_sha)
    db_session.commit()

    _drift_manifest_prompt_sha(db_session, run, drifted_stage)

    fresh_ai = _ai_for(keywords)
    with pytest.raises(StageContextMismatch):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=fresh_ai, firm_block_sha256=firm_sha)

    # Sessizce yeniden kullanim YOK: bozulan baglamla o asamaya hic
    # AI cagrisi YAPILMAMIS olmali (fail-closed, DAHA OKUMADAN durur).
    assert fresh_ai.call_counts[drifted_stage] == 0
    assert fresh_ai.call_counts[STAGE_URLGROUP] == 0


# ---------------------------------------------------------------------------
# K17: eksik ID hedefli tekrardan sonra da eksikse hata; 2. gecis BASLAMAZ
# ---------------------------------------------------------------------------


def test_missing_id_in_relevance_stage_raises_and_downstream_never_runs(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    k5 = keywords[4]
    # Ilk cevap yalniz ilk 4'u tasir; hedefli tekrar de k5'i getirmiyor.
    first = _ok({"results": [
        {"id": kw.id, "band": "strong", "relevance": 0.90} for kw in keywords[:4]
    ]})
    retry = _ok({"results": []})
    ai = FakeSeoAI({STAGE_REL: [first, retry]})
    calls = _assert_second_pass_never_started(monkeypatch)

    with pytest.raises(SeoStageError, match="checkpoint olusturulmaz"):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=ai, firm_block_sha256=firm_sha)

    assert ai.call_counts[STAGE_BP] == 0
    assert ai.call_counts[STAGE_SUBINTENT] == 0
    assert ai.call_counts[STAGE_AUTHORITY] == 0
    assert ai.call_counts[STAGE_URLGROUP] == 0
    assert calls == [], "REL asamasi patladigi halde 2. gecis (run_two_pass) baslamis"

    rel_rows = (db_session.query(EngineStageResult)
               .filter(EngineStageResult.scoring_run_id == run.id,
                       EngineStageResult.stage == STAGE_REL).all())
    assert rel_rows == [], "REL asamasi basarisiz oldugu halde satir yazilmis"


def test_missing_id_in_bp_stage_raises_and_downstream_never_runs(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    k1, k2, k3, k4 = keywords[0], keywords[1], keywords[2], keywords[3]
    # k4 icin bp cevabi EKSIK; hedefli tekrar de getirmiyor.
    bp_first = _ok({"results": [
        {"id": k1.id, "band": "very_close", "business_proximity": 0.85},
        {"id": k2.id, "band": "very_close", "business_proximity": 0.85},
        {"id": k3.id, "band": "support", "business_proximity": 0.72},
    ]})
    bp_retry = _ok({"results": []})
    ai = FakeSeoAI({STAGE_REL: [_ok(_rel_response(keywords))],
                    STAGE_BP: [bp_first, bp_retry]})
    calls = _assert_second_pass_never_started(monkeypatch)

    with pytest.raises(SeoStageError, match="checkpoint olusturulmaz"):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=ai, firm_block_sha256=firm_sha)

    assert ai.call_counts[STAGE_SUBINTENT] == 0
    assert ai.call_counts[STAGE_AUTHORITY] == 0
    assert ai.call_counts[STAGE_URLGROUP] == 0
    assert calls == [], "BP asamasi patladigi halde 2. gecis (run_two_pass) baslamis"


def test_missing_id_in_subintent_stage_raises_and_downstream_never_runs(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    k1, k2 = keywords[0], keywords[1]
    # famA cevabi k3'u ATLIYOR. `_unit_stage` artik checkpoint'ten ONCE
    # dogrular: eksik ID icin YALNIZ o birim (famA) icin 1 hedefli tekrar
    # dener; bu tekrar da k3'u getirmezse SeoStageError verir ve famA icin
    # HICBIR satir yazilmaz. famB kuyrukta UCUNCU sirada — famA basarisiz
    # oldugu icin famB'ye HIC sira gelmemeli (asagida call_counts ile
    # kanitlanir: STAGE_SUBINTENT tam 2 kez cagrilir, famA-ilk + famA-tekrar).
    sub_famA_partial = _ok({"results": [
        {"id": k1.id, "broad_intent": "informational", "subintent_id": "s1",
         "subintent_label": "S1"},
        {"id": k2.id, "broad_intent": "informational", "subintent_id": "s2",
         "subintent_label": "S2"},
    ]})
    # Hedefli tekrar YALNIZ eksik ID'yi (k3) sorar; bu tekrarin cevabi da onu
    # getirmezse "eksik" kalir. k1 gibi ZATEN istenmeyen bir ID donseydi
    # `_ask_unit` bunu "istenmeyen id" (yapisal hata) sayardi — o YANLIS
    # senaryo asagidaki ayri "unexpected_id" testinde zaten kapsanir.
    sub_famA_retry_still_missing = _ok({"results": []})
    ai = FakeSeoAI({STAGE_REL: [_ok(_rel_response(keywords))],
                    STAGE_BP: [_ok(_bp_response(keywords))],
                    STAGE_SUBINTENT: [sub_famA_partial, sub_famA_retry_still_missing,
                                      _ok(_subintent_response_famB(keywords))]})
    calls = _assert_second_pass_never_started(monkeypatch)

    with pytest.raises(SeoStageError, match="checkpoint OLUSTURULMAZ"):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=ai, firm_block_sha256=firm_sha)

    assert ai.call_counts[STAGE_SUBINTENT] == 2, (
        "famA icin ilk cagri + 1 hedefli tekrar; famB'ye HIC sira gelmemeli")
    assert ai.call_counts[STAGE_AUTHORITY] == 0
    assert ai.call_counts[STAGE_URLGROUP] == 0
    assert calls == [], "SUBINTENT asamasi patladigi halde 2. gecis baslamis"

    subintent_rows = (db_session.query(EngineStageResult)
                      .filter(EngineStageResult.scoring_run_id == run.id,
                              EngineStageResult.stage == STAGE_SUBINTENT).all())
    assert subintent_rows == [], (
        "famA icin checkpoint YAZILMAMALIYDI (K17: yarim/basarisiz birim "
        "kalicilasmaz)")


def test_missing_id_in_authority_stage_raises_and_downstream_never_runs(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    auth_first = _ok({"results": []})     # k3 eksik
    auth_retry = _ok({"results": []})     # hedefli tekrar de getirmiyor
    ai = FakeSeoAI({STAGE_REL: [_ok(_rel_response(keywords))],
                    STAGE_BP: [_ok(_bp_response(keywords))],
                    STAGE_SUBINTENT: [_ok(_subintent_response_famA(keywords)),
                                      _ok(_subintent_response_famB(keywords))],
                    STAGE_AUTHORITY: [auth_first, auth_retry]})
    calls = _assert_second_pass_never_started(monkeypatch)

    with pytest.raises(SeoStageError, match="checkpoint olusturulmaz"):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=ai, firm_block_sha256=firm_sha)

    assert ai.call_counts[STAGE_URLGROUP] == 0
    assert calls == [], "AUTHORITY asamasi patladigi halde 2. gecis baslamis"


def test_missing_id_in_urlgroup_stage_raises_and_second_pass_never_starts(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    workspace, run, keywords, universe = _make_frozen_universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()

    k1, k2 = keywords[0], keywords[1]
    # famA icin url grubu cevabi k3'u ATLIYOR. `_unit_stage` artik checkpoint'ten
    # ONCE dogrular: eksik ID icin YALNIZ famA icin 1 hedefli tekrar dener; bu
    # tekrar da k3'u getirmezse SeoStageError verir ve famA icin HICBIR satir
    # yazilmaz (K17). Sadece famA var (famB url_units'e hic girmiyor), o yuzden
    # STAGE_URLGROUP tam 2 kez cagrilir (ilk + hedefli tekrar).
    url_partial = _ok({"groups": [{"group_id": "g1", "ids": [k1.id, k2.id]}]})
    # Hedefli tekrar YALNIZ eksik ID'yi (k3) sorar; bos donmesi "hala eksik"
    # anlamina gelir — k1/k2 gibi zaten istenmeyen bir ID donseydi bu
    # "istenmeyen id" (ayri testte kapsanan) yapisal hatayla karisirdi.
    url_retry_still_missing = _ok({"groups": []})
    ai = FakeSeoAI({STAGE_REL: [_ok(_rel_response(keywords))],
                    STAGE_BP: [_ok(_bp_response(keywords))],
                    STAGE_SUBINTENT: [_ok(_subintent_response_famA(keywords)),
                                      _ok(_subintent_response_famB(keywords))],
                    STAGE_AUTHORITY: [_ok(_authority_response(keywords))],
                    STAGE_URLGROUP: [url_partial, url_retry_still_missing]})
    calls = _assert_second_pass_never_started(monkeypatch)

    with pytest.raises(SeoStageError, match="checkpoint OLUSTURULMAZ"):
        run_seo_stage(db_session, run=run, profile=PROFILE, universe=universe.rows,
                     family_by_id=_family_by_id(keywords), families=_families(),
                     ai=ai, firm_block_sha256=firm_sha)

    assert ai.call_counts[STAGE_URLGROUP] == 2, (
        "famA icin ilk cagri + 1 hedefli tekrar")
    urlgroup_rows = (db_session.query(EngineStageResult)
                     .filter(EngineStageResult.scoring_run_id == run.id,
                             EngineStageResult.stage == STAGE_URLGROUP).all())
    assert urlgroup_rows == [], (
        "famA icin checkpoint YAZILMAMALIYDI (K17: yarim/basarisiz birim "
        "kalicilasmaz)")
    assert calls == [], (
        "URLGROUP asamasi patladigi halde 2. gecis (run_two_pass) baslamis — "
        "secim YAZILMAMALIYDI")


# ---------------------------------------------------------------------------
# K17 checkpoint-once-validated (Codex incelemesi): `_unit_stage` artik
# checkpoint'e YAZMADAN ONCE beklenen ID kumesine karsi dogruluyor. Bu blok
# `_unit_stage`'i SUBINTENT ve URLGROUP sozlesmeleriyle AYRI AYRI (id_field
# "id" / "ids", result_key "results" / "groups") dogrudan calistirir —
# mekanizma paylasimli oldugu icin `run_seo_stage`'in butun boru hattini
# yeniden kurmadan hedefli tekrar/yabanci-ID/kismi-birim davranisini
# izole eder.
# ---------------------------------------------------------------------------

UNIT_STAGE_CASES: Dict[str, Dict[str, Any]] = {
    "subintent": {
        "stage": STAGE_SUBINTENT, "scope_type": SCOPE_FAMILY, "id_field": "id",
        "result_key": "results", "schema": P.SUBINTENT_SCHEMA,
        "item": lambda kid: {"id": kid, "broad_intent": "informational",
                             "subintent_id": f"s{kid}", "subintent_label": f"S{kid}"},
    },
    "urlgroup": {
        "stage": STAGE_URLGROUP, "scope_type": SCOPE_URL_GROUP, "id_field": "ids",
        "result_key": "groups", "schema": P.URLGROUP_SCHEMA,
        "item": lambda kid: {"group_id": f"g{kid}", "ids": [kid]},
    },
}


def _items_for(case_name: str, ids: List[int]) -> List[Dict[str, Any]]:
    case = UNIT_STAGE_CASES[case_name]
    return [case["item"](kid) for kid in ids]


def _unit_response(case_name: str, ids: List[int]) -> str:
    case = UNIT_STAGE_CASES[case_name]
    return _ok({case["result_key"]: _items_for(case_name, ids)})


def _empty_response(case_name: str) -> str:
    """Bir hedefli tekrarin YINE eksik kalmasini simule eder — YALNIZ eksik
    ID'nin sorulduğu bir tekrar cagrisinda, eksik-DISI (zaten bilinen) bir
    ID döndürmek `_ask_unit`'in "istenmeyen id" (yapisal hata) dalini
    tetikler; "hala eksik" senaryosunu SAF haliyle test etmek icin bos
    cevap kullanilir."""
    case = UNIT_STAGE_CASES[case_name]
    return _ok({case["result_key"]: []})


def _recording_build_prompt(record: List[List[int]]):
    def build(ids: Any) -> str:
        record.append(sorted(int(i) for i in ids))
        return "prompt:" + str(sorted(int(i) for i in ids))
    return build


def _make_unit(scope_key: str, ids: List[int],
              record: Optional[List[List[int]]] = None) -> Dict[str, Any]:
    return {"scope_key": scope_key, "ids": list(ids),
            "build_prompt": _recording_build_prompt(
                record if record is not None else [])}


def _call_unit_stage(db_session, run, ai: FakeSeoAI, case_name: str,
                     units: List[Dict[str, Any]], firm_sha: str) -> Dict[str, Any]:
    case = UNIT_STAGE_CASES[case_name]
    return R._unit_stage(db_session, run=run, stage=case["stage"],
                         scope_type=case["scope_type"], units=units,
                         schema=case["schema"], max_tokens=8000,
                         result_key=case["result_key"], id_field=case["id_field"],
                         ai=ai, firm_sha=firm_sha)


def _sealed_run(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, algorithm_version="v3")
    firm_sha = _firm_sha()
    _seal_seo_manifest(run, firm_sha=firm_sha)
    db_session.commit()
    return run, firm_sha


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_targeted_retry_recovers_missing_id_and_calls_ai_exactly_twice(
    db_session, make_workspace, make_scoring_run, case_name,
):
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]

    prompt_calls: List[List[int]] = []
    unit = _make_unit("u1", [101, 102, 103], prompt_calls)

    first = _unit_response(case_name, [101, 102])        # 103 eksik
    retry = _unit_response(case_name, [103])              # hedefli tekrar 103'u getiriyor
    ai = FakeSeoAI({case["stage"]: [first, retry]})

    out = _call_unit_stage(db_session, run, ai, case_name, [unit], firm_sha)

    assert ai.call_counts[case["stage"]] == 2, (
        "ilk cagri + tam olarak 1 hedefli tekrar bekleniyor")
    assert prompt_calls[0] == [101, 102, 103], "ilk cagri TUM beklenen ID'leri istemeli"
    assert prompt_calls[1] == [103], (
        "hedefli tekrar YALNIZ eksik ID'yi istemeli, tum kumeyi DEGIL")

    assert "u1" in out
    row = (db_session.query(EngineStageResult)
          .filter(EngineStageResult.scoring_run_id == run.id,
                  EngineStageResult.stage == case["stage"],
                  EngineStageResult.scope_key == "u1").one())
    combined_ids = set(R._ids_in_items(row.payload["items"], case["id_field"]))
    assert combined_ids == {101, 102, 103}, (
        "birlesik sonuc (ilk + hedefli tekrar) tum beklenen ID'leri tasimali")


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_still_missing_after_targeted_retry_raises_and_writes_no_row(
    db_session, make_workspace, make_scoring_run, case_name,
):
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]
    unit = _make_unit("u1", [101, 102, 103])

    first = _unit_response(case_name, [101, 102])        # 103 eksik
    retry = _empty_response(case_name)                    # hedefli tekrar de 103'u getirmiyor
    ai = FakeSeoAI({case["stage"]: [first, retry]})

    with pytest.raises(SeoStageError, match="checkpoint OLUSTURULMAZ"):
        _call_unit_stage(db_session, run, ai, case_name, [unit], firm_sha)

    assert ai.call_counts[case["stage"]] == 2

    rows = (db_session.query(EngineStageResult)
           .filter(EngineStageResult.scoring_run_id == run.id,
                   EngineStageResult.stage == case["stage"]).all())
    assert rows == [], "eksik birim icin checkpoint YAZILMIS olmamali (K17)"


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_unexpected_id_raises_and_retry_never_attempted(
    db_session, make_workspace, make_scoring_run, case_name,
):
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]
    unit = _make_unit("u1", [101, 102])

    # 101, 102 VE beklenmeyen 999 — kuyrukta TEK cevap: hedefli tekrar
    # DENENIRSE FakeSeoAI bos kuyruktan AssertionError firlatir ve bu test
    # KIRMIZI olurdu; yani "tek cevap yeterli" testin kendisi ispat.
    bad = _ok({case["result_key"]: _items_for(case_name, [101, 102, 999])})
    ai = FakeSeoAI({case["stage"]: [bad]})

    with pytest.raises(SeoStageError, match="istenmeyen id"):
        _call_unit_stage(db_session, run, ai, case_name, [unit], firm_sha)

    assert ai.call_counts[case["stage"]] == 1, (
        "yabanci ID yapisal hatadir — hedefli tekrar DENENMEMELI")

    rows = (db_session.query(EngineStageResult)
           .filter(EngineStageResult.scoring_run_id == run.id,
                   EngineStageResult.stage == case["stage"]).all())
    assert rows == [], "yabanci ID doneminde checkpoint YAZILMAMALIYDI"


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_non_numeric_id_raises_structural_error(
    db_session, make_workspace, make_scoring_run, case_name,
):
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]
    unit = _make_unit("u1", [101])

    if case_name == "subintent":
        bad_item = {"id": "abc", "broad_intent": "informational",
                    "subintent_id": "s1", "subintent_label": "S1"}
    else:
        bad_item = {"group_id": "g1", "ids": ["abc"]}
    ai = FakeSeoAI({case["stage"]: [_ok({case["result_key"]: [bad_item]})]})

    with pytest.raises(SeoStageError, match="sayisal olmayan id"):
        _call_unit_stage(db_session, run, ai, case_name, [unit], firm_sha)


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_partial_failure_isolates_previously_successful_units(
    db_session, make_workspace, make_scoring_run, case_name,
):
    """3 birim: 1. ve 2. basarili, 3. hedefli tekrardan sonra da eksik kalir.
    Hata sonrasi DB'de YALNIZ 1. ve 2. birimin satirlari bulunmali."""
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]
    unit1 = _make_unit("u1", [101])
    unit2 = _make_unit("u2", [201])
    unit3 = _make_unit("u3", [301, 302])

    ok1 = _unit_response(case_name, [101])
    ok2 = _unit_response(case_name, [201])
    bad3_first = _unit_response(case_name, [301])          # 302 eksik
    bad3_retry = _empty_response(case_name)                 # hedefli tekrar de getirmiyor
    ai = FakeSeoAI({case["stage"]: [ok1, ok2, bad3_first, bad3_retry]})

    with pytest.raises(SeoStageError, match="checkpoint OLUSTURULMAZ"):
        _call_unit_stage(db_session, run, ai, case_name, [unit1, unit2, unit3], firm_sha)

    scope_keys = {
        row.scope_key for row in db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == case["stage"]).all()
    }
    assert scope_keys == {"u1", "u2"}, (
        "3. birim basarisiz oldugu halde DAHA ONCE basarili olan 1. ve 2. "
        "birimlerin checkpoint'i ETKILENMEMELI, 3.'nunki de HIC yazilmamali")


@pytest.mark.parametrize("case_name", ["subintent", "urlgroup"])
def test_unit_stage_resume_after_failure_does_not_reask_successful_units_and_completes(
    db_session, make_workspace, make_scoring_run, case_name,
):
    """Bozuk cache kullanilmaz + duzeltilmis cevapla devam eder (Codex 5. madde):
    ayni run'i ikinci kez calistirmak, basarisiz birim icin checkpoint
    OLMADIGINDAN yeniden sorar; bu kez sahte AI dogru cevabi verince asama
    BASARIYLA TAMAMLANIR. Basarili birimler (u1, u2) YENIDEN SORULMAZ."""
    run, firm_sha = _sealed_run(db_session, make_workspace, make_scoring_run)
    case = UNIT_STAGE_CASES[case_name]

    unit1 = _make_unit("u1", [101])
    unit2 = _make_unit("u2", [201])
    unit3 = _make_unit("u3", [301, 302])

    ok1 = _unit_response(case_name, [101])
    ok2 = _unit_response(case_name, [201])
    bad3_first = _unit_response(case_name, [301])           # 302 eksik
    bad3_retry = _empty_response(case_name)                  # hedefli tekrar de getirmiyor
    ai1 = FakeSeoAI({case["stage"]: [ok1, ok2, bad3_first, bad3_retry]})

    with pytest.raises(SeoStageError, match="checkpoint OLUSTURULMAZ"):
        _call_unit_stage(db_session, run, ai1, case_name, [unit1, unit2, unit3], firm_sha)

    # ikinci kosu: TAZE ai, AYNI run/db — u1/u2 checkpoint'ten donmeli
    # (yeniden SORULMAMALI), u3 bu kez DUZELTILMIS/TAM cevapla tamamlanmali.
    unit1_again = _make_unit("u1", [101])
    unit2_again = _make_unit("u2", [201])
    unit3_again = _make_unit("u3", [301, 302])

    fixed3 = _unit_response(case_name, [301, 302])           # bu kez TAM cevap
    ai2 = FakeSeoAI({case["stage"]: [fixed3]})

    out = _call_unit_stage(db_session, run, ai2, case_name,
                           [unit1_again, unit2_again, unit3_again], firm_sha)

    assert ai2.call_counts[case["stage"]] == 1, (
        "basarili birimler (u1, u2) checkpoint'ten DONMELI, yeniden "
        "SORULMAMALI — bozuk/eksik onceki deneme sonraki kosuyu kirletmemeli")
    assert set(out) == {"u1", "u2", "u3"}

    rows = (db_session.query(EngineStageResult)
           .filter(EngineStageResult.scoring_run_id == run.id,
                   EngineStageResult.stage == case["stage"]).all())
    assert {r.scope_key for r in rows} == {"u1", "u2", "u3"}, (
        "ikinci kosu sonunda ucunun de checkpoint'i tam olmali")
