"""Family V2 runner entegrasyon testleri (`app/core/engine/family/runner.py`).

QA gorevi: Motor v3 Faz 2 = Family V2 portu. Butun AI etkilesimi SAHTE bir
istemciyle kurulur (`FakeFamilyAI`); GERCEK saglayiciya HICBIR cagri
yapilmaz. Sozlesme: A1 -> A2 (25'lik batch, sozluk disi family_id ->
UNMATCHED) -> A2B (yalniz YENI AILE onerir, kelime ATAMAZ; destek >= 2)
-> sozluk DONAR -> A2C (butun UNMATCHED yeniden atanir) -> A3 (15'lik
batch) -> kapanis (`single:<id>` + 0 uyeli aile budama). Butun asama
satirlari `engine_stage_results`'a `scope_type="family"` ile yazilir;
resume mühürlü baglamla (model + prompt SHA + firm_block SHA) dogrulanir.

GUNCELLEME (Codex incelemesi sonrasi, iki bloklayici duzeltildi):
  * A2/A2C artik `confidence`'i da saklar (K17 ortak katmani `app.core.
    engine.ai_runner.run_batch` ile).
  * A3 artik kilitli davranista: secim TUM EVRENDEN `confidence != "high"`
    VE `family_id != UNMATCHED`; UNMATCHED kelimeler A3'e HIC GITMEZ (onlar
    kapanista `single:<id>` olur); UNMATCHED hic olmasa bile dusuk guvenli
    atama varsa A3 KOSAR. `a3_candidates()` gonderiyor (mevcut aile + ayni
    solution_type'tan en fazla 3 aile, en fazla 4 oge, butun sozluk DEGIL).
    A3 sozluk disi family_id donerse MEVCUT ATAMA KORUNUR (UNMATCHED'a
    CEVRILMEZ — eski davranistan farkli).
  * Sahte AI, `AI.run_batch`'in hedefli tekrar mekanizmasini tetiklememek
    icin her batch'te ISTENEN TUM ID'leri TEK cevapta dondurur (K17
    tekrar/eksik-ID testleri ayri dosyada: `tests/unit/test_engine_ai_runner.py`).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

import pytest

from app.core.engine.family import prompts as P
from app.core.engine.family import rules as R
from app.core.engine.family.runner import (
    FAMILY_MODEL,
    SCOPE_FAMILY,
    STAGE_A1,
    STAGE_A2,
    STAGE_A2B,
    STAGE_A2C,
    STAGE_A3,
    a3_candidates,
    family_models,
    family_prompt_shas,
    run_family_stage,
)
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
    "sector": "kozmetik",
    "brand_name": "Digitus",
    "brand_summary": "Beyaz saç bakım ürünleri satan marka",
    "products": ["şampuan"],
}


@dataclass
class Row:
    keyword_id: int
    keyword_text: str


def _firm_sha(profile: Dict[str, Any] = PROFILE) -> str:
    return hashlib.sha256(P.firm_block(profile).encode("utf-8")).hexdigest()


def _seal_family_manifest(run, *, firm_sha: str = None) -> None:
    seal_manifest(
        run,
        firm_block_sha256=firm_sha or _firm_sha(),
        algorithm_versions={"family": P.FAMILY_V2_VERSION},
        models=family_models(),
        prompt_shas=family_prompt_shas(),
        location_policy=_loc_snap({}),
    )


class _ScopedFakeAI:
    def __init__(self, parent: "FakeFamilyAI", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def complete_json(self, prompt: str, max_tokens: int = None,
                       response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond(self._stage, prompt)


class FakeFamilyAI:
    """`complete_json` cagrilarini SAYAN + sirayla hazir JSON dondüren sahte istemci.

    GERCEK saglayiciya ASLA gitmez. `for_stage(stage, model=..., thinking_level=...)`
    StageScopedAIService desenini destekler (runner `hasattr(ai, "for_stage")`
    kontrolu yapar ve varsa onu kullanir).
    """

    def __init__(self, responses: Dict[str, List[Dict[str, Any]]]) -> None:
        self._queues: Dict[str, List[Dict[str, Any]]] = {
            stage: list(payloads) for stage, payloads in responses.items()
        }
        self.calls: List[tuple] = []
        self.call_counts: Counter = Counter()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "_ScopedFakeAI":
        return _ScopedFakeAI(self, stage)

    def _respond(self, stage: str, prompt: str) -> str:
        self.calls.append((stage, prompt))
        self.call_counts[stage] += 1
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"FakeFamilyAI: '{stage}' icin kuyrukta hazir cevap kalmadi "
                f"(bu {self.call_counts[stage]}. cagri) — GERCEK saglayiciya "
                "gidilmeye CALISILIYOR olabilir, bu test bunu engeller."
            )
        return json.dumps(queue.pop(0), ensure_ascii=False)

    @property
    def total_calls(self) -> int:
        return sum(self.call_counts.values())


def _batched_unmatched_results(rows: Sequence[Row], batch_size: int = R.ASSIGN_BATCH
                               ) -> List[Dict[str, Any]]:
    """Butun satirlarin UNMATCHED donduruldugu A2/A2C cevap kuyrugu (batch'lenmis)."""
    payloads = []
    for index in range(0, len(rows), batch_size):
        batch = rows[index:index + batch_size]
        payloads.append({
            "results": [
                {"id": row.keyword_id, "family_id": R.UNMATCHED, "confidence": "low"}
                for row in batch
            ]
        })
    return payloads


# ---------------------------------------------------------------------------
# Muhurleme sozlesmesi — rules.py kusurundan BAGIMSIZ, bugun de GECMELI
# ---------------------------------------------------------------------------


def test_family_manifest_sealed_with_family_prompt_shas_and_models_matches_runner_context(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    section = manifest_section(run)
    assert section["models"] == family_models()
    assert section["prompt_shas"] == family_prompt_shas()
    assert section["firm_block_sha256"] == _firm_sha()

    for stage in (STAGE_A1, STAGE_A2, STAGE_A2B, STAGE_A2C, STAGE_A3):
        ctx = StageContext(
            model=FAMILY_MODEL,
            prompt_sha=family_prompt_shas()[stage],
            firm_block_sha256=_firm_sha(),
        )
        verify_stage_context(run, stage, ctx)  # patlamamali


# ---------------------------------------------------------------------------
# Tam akis: A1 -> A2 -> A2B -> A2C -> kapanis
# ---------------------------------------------------------------------------


def _two_family_scenario():
    """kw1/kw2 -> beyaz_sac_sampuani (A2); kw3/kw4 -> A2'de UNMATCHED,
    A2B'de yeni aile 'protein_tozu' (destek 2, kabul) + 'az_destekli'
    (destek 1, red) onerilir; A2C her ikisini de protein_tozu'na atar.
    """
    rows = [
        Row(1, "beyaz saç kapatıcı şampuan"),
        Row(2, "beyaz saçlar için şampuan"),
        Row(3, "protein tozu fiyatları"),
        Row(4, "whey protein çilek aromalı"),
    ]

    a1 = {"families": [{
        "family_id": "beyaz_sac_sampuani",
        "family_name": "Beyaz Saç Şampuanı",
        "core_need": "beyaz saçı kapatma ihtiyacı",
        "solution_type": "şampuan",
        "entity": "beyaz saç",
        "examples": ["beyaz saç kapatıcı şampuan", "beyaz saçlar için şampuan"],
        "do_not_confuse": [],
    }]}

    a2 = {"results": [
        {"id": 1, "family_id": "beyaz_sac_sampuani", "confidence": "high"},
        {"id": 2, "family_id": "beyaz_sac_sampuani", "confidence": "high"},
        {"id": 3, "family_id": R.UNMATCHED, "confidence": "low"},
        # sozluk DISI bir family_id — _assign_batches bunu UNMATCHED'a cevirmeli.
        {"id": 4, "family_id": "hayali_sozluk_disi_aile", "confidence": "low"},
    ]}

    a2b = {"new_families": [
        {
            "family_id": "protein_tozu",
            "family_name": "Protein Tozu",
            "core_need": "kas gelişimi için protein alımı",
            "solution_type": "toz takviye",
            "entity": "whey protein",
            "examples": ["protein tozu fiyatları", "whey protein çilek aromalı"],
            "do_not_confuse": [],
        },
        {
            # Destek yalniz 1 (yalniz kw3 metniyle eslesiyor) -> REDDEDILMELI.
            "family_id": "az_destekli_aile",
            "family_name": "Az Destekli",
            "core_need": "baska bir ihtiyac",
            "solution_type": "baska cozum",
            "entity": "baska",
            "examples": ["protein tozu fiyatları"],
            "do_not_confuse": [],
        },
    ]}

    # confidence="high" KASITLI: bu senaryo A3'u tetiklemez (A3 testleri ayri).
    a2c = {"results": [
        {"id": 3, "family_id": "protein_tozu", "confidence": "high"},
        {"id": 4, "family_id": "protein_tozu", "confidence": "high"},
    ]}

    responses = {
        STAGE_A1: [a1],
        STAGE_A2: [a2],
        STAGE_A2B: [a2b],
        STAGE_A2C: [a2c],
    }
    return rows, responses


def test_run_family_stage_full_flow_covers_universe(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows, responses = _two_family_scenario()
    ai = FakeFamilyAI(responses)

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    # Evrendeki HER kelime bir aile alir.
    assert set(result["family_by_id"]) == {1, 2, 3, 4}
    assert result["family_by_id"][1] == "beyaz_sac_sampuani"
    assert result["family_by_id"][2] == "beyaz_sac_sampuani"
    assert result["family_by_id"][3] == "protein_tozu"
    assert result["family_by_id"][4] == "protein_tozu"
    assert result["single_family_keyword_ids"] == []
    assert result["unmatched_after_a2"] == 2
    assert result["low_confidence_reviewed"] == 0  # hepsi confidence="high"

    # A2B: destek-2 kabul edildi, destek-1 reddedildi ve sozluge girmedi.
    assert result["a2b"]["accepted"] == {"protein_tozu": [3, 4]}
    assert result["a2b"]["rejected"] == {"az_destekli_aile": [3]}
    assert "az_destekli_aile" not in {f["family_id"] for f in result["families"]}

    # Butun asama satirlari scope_type="family" ile yazildi.
    rows_in_db = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .all()
    )
    assert rows_in_db, "hicbir asama satiri yazilmadi"
    assert {r.scope_type for r in rows_in_db} == {"family"}
    assert {r.stage for r in rows_in_db} == {STAGE_A1, STAGE_A2, STAGE_A2B, STAGE_A2C}


def test_dictionary_sha_frozen_after_a2b_matches_finalized_dictionary_sha(
    db_session, make_workspace, make_scoring_run,
):
    """Sozluk donmasi: A2B sonrasi mühürlenen SHA, kapanistaki (A2C sonrasi)
    sozluk SHA'siyla AYNI olmalidir (bu senaryoda hicbir aile bos kalmiyor)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows, responses = _two_family_scenario()
    ai = FakeFamilyAI(responses)

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    assert result["a2b"]["dictionary_sha256_frozen"] == result["dictionary_sha256"]


# ---------------------------------------------------------------------------
# A2: sozluk disi family_id -> UNMATCHED (backfill/yeni aile YOK)
# ---------------------------------------------------------------------------


def test_stage_a2_out_of_dictionary_family_id_counted_as_unmatched(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows, responses = _two_family_scenario()
    ai = FakeFamilyAI(responses)

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    # kw4 A2'de sozluk DISI "hayali_sozluk_disi_aile" aldi; bu id NIHAI
    # sonuca hic gecmemeli (A2C onu protein_tozu'na tasidi).
    assert result["family_by_id"][4] != "hayali_sozluk_disi_aile"
    assert "hayali_sozluk_disi_aile" not in {f["family_id"] for f in result["families"]}
    assert result["unmatched_after_a2"] == 2  # kw3 (UNMATCHED) + kw4 (sozluk disi)


# ---------------------------------------------------------------------------
# A3 (kilitli davranis, Codex incelemesi sonrasi): dusuk-guven ikinci tur
# ---------------------------------------------------------------------------


def test_a3_runs_on_low_confidence_even_without_any_unmatched(
    db_session, make_workspace, make_scoring_run,
):
    """UNMATCHED YOK ama dusuk guvenli atama VAR -> A3 GERCEKTEN CALISIR.

    A2 iki kelimeyi de atar (hicbiri UNMATCHED degil); biri confidence
    "high", digeri "medium". A2B/A2C hic cagrilmamali (unmatched bos oldugu
    icin); A3 sahte istemcinin `family_a3` cagri sayaciyla VE DB'deki
    `family_a3` satirlariyla KANITLANIR.
    """
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows = [Row(1, "aile z birinci kelime"), Row(2, "aile z ikinci kelime")]
    a1 = {"families": [{
        "family_id": "aile_z", "family_name": "Aile Z", "core_need": "n",
        "solution_type": "s", "entity": "e",
        "examples": ["aile z birinci kelime", "aile z ikinci kelime"],
        "do_not_confuse": [],
    }]}
    a2 = {"results": [
        {"id": 1, "family_id": "aile_z", "confidence": "high"},
        {"id": 2, "family_id": "aile_z", "confidence": "medium"},
    ]}
    a3 = {"results": [{"id": 2, "family_id": "aile_z", "reason": "onaylandi"}]}

    ai = FakeFamilyAI({STAGE_A1: [a1], STAGE_A2: [a2], STAGE_A3: [a3]})

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    assert result["unmatched_after_a2"] == 0
    assert result["a2b"] == {}  # A2B/A2C hic calismadi
    assert result["low_confidence_reviewed"] == 1
    assert ai.call_counts[STAGE_A2B] == 0
    assert ai.call_counts[STAGE_A2C] == 0
    assert ai.call_counts[STAGE_A3] == 1

    a3_rows = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_A3)
        .all()
    )
    assert len(a3_rows) == 1
    assert a3_rows[0].scope_key == "2"
    assert result["family_by_id"][2] == "aile_z"


def test_unmatched_keyword_never_sent_to_a3_and_becomes_single(
    db_session, make_workspace, make_scoring_run,
):
    """UNMATCHED kalan kelime A3'e HIC GONDERILMEZ; kapanista `single:<id>`
    olur. Ayni kosuda BASKA bir dusuk-guvenli (ama ATANMIS) kelime A3'e
    GIRER — boylece A3'un GERCEKTEN kostugunu ve UNMATCHED kelimeyi
    prompt'a HIC KOYMADIGINI ayni testte kanitlariz."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows = [
        Row(1, "aile x birinci kelime"),
        Row(2, "tamamen alakasiz sorgu"),
        Row(3, "aile x ucuncu kelime"),
    ]
    a1 = {"families": [{
        "family_id": "aile_x", "family_name": "Aile X", "core_need": "n",
        "solution_type": "s", "entity": "e",
        "examples": ["aile x birinci kelime", "aile x ucuncu kelime"],
        "do_not_confuse": [],
    }]}
    a2 = {"results": [
        {"id": 1, "family_id": "aile_x", "confidence": "high"},
        {"id": 2, "family_id": R.UNMATCHED, "confidence": "low"},
        {"id": 3, "family_id": "aile_x", "confidence": "low"},
    ]}
    a2b = {"new_families": []}  # hicbir yeni aile onerilmiyor
    a2c = {"results": [{"id": 2, "family_id": R.UNMATCHED, "confidence": "low"}]}
    a3 = {"results": [{"id": 3, "family_id": "aile_x", "reason": "onaylandi"}]}

    ai = FakeFamilyAI({
        STAGE_A1: [a1], STAGE_A2: [a2], STAGE_A2B: [a2b],
        STAGE_A2C: [a2c], STAGE_A3: [a3],
    })

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    assert ai.call_counts[STAGE_A3] == 1
    a3_prompt = next(prompt for stage, prompt in ai.calls if stage == STAGE_A3)
    assert "tamamen alakasiz sorgu" not in a3_prompt  # kw2 (UNMATCHED) A3'e GITMEDI
    assert "aile x ucuncu kelime" in a3_prompt        # kw3 (dusuk guven) A3'e GIRDI

    assert result["family_by_id"][2] == R.single_family_id(2)
    assert 2 in result["single_family_keyword_ids"]
    assert result["family_by_id"][3] == "aile_x"


def test_a3_out_of_dictionary_family_id_preserves_current_assignment(
    db_session, make_workspace, make_scoring_run,
):
    """A3 sozluk DISI bir family_id donerse MEVCUT ATAMA KORUNUR (eski
    davranistan FARKLI: artik UNMATCHED'a CEVRILMEZ, yeni aile de ACILMAZ)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows = [Row(1, "tek kelime")]
    a1 = {"families": [{
        "family_id": "aile_only", "family_name": "Tek Aile", "core_need": "n",
        "solution_type": "s", "entity": "e", "examples": ["tek kelime"],
        "do_not_confuse": [],
    }]}
    a2 = {"results": [{"id": 1, "family_id": "aile_only", "confidence": "low"}]}
    a3 = {"results": [{"id": 1, "family_id": "gecersiz_sozluk_disi_aile",
                       "reason": "uydurma"}]}

    ai = FakeFamilyAI({STAGE_A1: [a1], STAGE_A2: [a2], STAGE_A3: [a3]})

    result = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    assert result["family_by_id"][1] == "aile_only"          # KORUNDU
    assert result["family_by_id"][1] != "gecersiz_sozluk_disi_aile"
    assert result["family_by_id"][1] != R.single_family_id(1)  # UNMATCHED'a DUSMEDI
    assert "gecersiz_sozluk_disi_aile" not in {f["family_id"] for f in result["families"]}


def test_a3_candidates_capped_at_four_and_restricted_to_current_solution_type():
    """`a3_candidates`: mevcut aile + AYNI solution_type'tan en fazla UC aile,
    tekillestirilip EN FAZLA DORT oge; farkli solution_type'taki aileler
    GIRMEZ; mevcut aile HER ZAMAN ilk sirada; bicim 'id | ad'."""
    def fam(fid, solution):
        return {"family_id": fid, "family_name": f"Ad {fid}",
                "core_need": "n", "solution_type": solution, "entity": "e",
                "examples": ["x"], "do_not_confuse": []}

    # 5 farkli aile AYNI solution_type'ta, mevcut aile listede SONRA gelir
    # (boylece same_solution[:3] mevcut aileyi TEKRAR SAYMAZ).
    families = [
        fam("f1", "cozum_a"), fam("f2", "cozum_a"), fam("f3", "cozum_a"),
        fam("f4", "cozum_a"), fam("f5", "cozum_a"),
        fam("f_current", "cozum_a"),
        fam("f_other", "cozum_b"),
    ]

    candidates = a3_candidates("f_current", families)

    assert len(candidates) == 4
    assert candidates[0] == "f_current | Ad f_current"  # mevcut aile ILK sirada
    assert candidates == ["f_current | Ad f_current", "f1 | Ad f1",
                          "f2 | Ad f2", "f3 | Ad f3"]
    for excluded in ("f4 |", "f5 |", "f_other |"):
        assert not any(c.startswith(excluded) for c in candidates)


def test_a3_candidates_does_not_duplicate_current_when_it_is_first_in_dictionary():
    """Mevcut aile listede AYNI solution_type grubunun icinde (ve ilk
    siralarda) de olsa, ciktida BIR KEZ gorunur — tekrar etmez."""
    def fam(fid, solution):
        return {"family_id": fid, "family_name": f"Ad {fid}",
                "core_need": "n", "solution_type": solution, "entity": "e",
                "examples": ["x"], "do_not_confuse": []}

    families = [fam("f_current", "cozum_a"), fam("f1", "cozum_a"),
                fam("f2", "cozum_a")]

    candidates = a3_candidates("f_current", families)

    assert candidates == ["f_current | Ad f_current", "f1 | Ad f1", "f2 | Ad f2"]
    assert candidates.count("f_current | Ad f_current") == 1


# ---------------------------------------------------------------------------
# Tavan: A2 sonrasi UNMATCHED > 300 -> FamilyStageError, akis durur
# ---------------------------------------------------------------------------


def test_unmatched_ceiling_exceeded_after_a2_raises_and_stops_flow(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows = [Row(i, f"kelime {i}") for i in range(1, 302)]  # 301 adet
    a1 = {"families": [{
        "family_id": "genel_aile",
        "family_name": "Genel",
        "core_need": "n",
        "solution_type": "s",
        "entity": "e",
        "examples": ["kelime 1", "kelime 2", "kelime 3"],
        "do_not_confuse": [],
    }]}
    a2_payloads = _batched_unmatched_results(rows)

    ai = FakeFamilyAI({STAGE_A1: [a1], STAGE_A2: a2_payloads})

    with pytest.raises(R.FamilyStageError, match="301"):
        run_family_stage(
            db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
            firm_block_sha256=_firm_sha(),
        )

    # A2B/A2C/A3 hic cagrilmamis olmali — akis A2 sonrasi durdu.
    assert ai.call_counts[STAGE_A2B] == 0
    assert ai.call_counts[STAGE_A2C] == 0
    assert ai.call_counts[STAGE_A3] == 0


# ---------------------------------------------------------------------------
# RESUME: ikinci cagri AI'yi TEKRAR cagirmaz, sonuc birebir aynidir
# ---------------------------------------------------------------------------


def test_run_family_stage_resume_does_not_call_ai_again_and_result_is_identical(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows, responses = _two_family_scenario()
    ai = FakeFamilyAI(responses)

    result1 = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )
    calls_after_first = ai.total_calls
    assert calls_after_first > 0

    result2 = run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )

    assert ai.total_calls == calls_after_first, (
        "ikinci run_family_stage cagrisi AI'yi TEKRAR cagirdi — resume "
        "engine_stage_results'tan okumuyor"
    )
    assert result1 == result2

    rows_in_db = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .all()
    )
    assert {r.scope_type for r in rows_in_db} == {"family"}


# ---------------------------------------------------------------------------
# Baglam uyusmazligi: manifest degisirse resume SessizCE tekrar kullanilmaz
# ---------------------------------------------------------------------------


def test_run_family_stage_resume_with_manifest_drift_raises_stage_context_mismatch(
    db_session, make_workspace, make_scoring_run,
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal_family_manifest(run)
    db_session.commit()

    rows, responses = _two_family_scenario()
    ai = FakeFamilyAI(responses)
    run_family_stage(
        db_session, run=run, profile=PROFILE, rows=rows, ai=ai,
        firm_block_sha256=_firm_sha(),
    )
    db_session.commit()

    # Run'in GERCEK muhurlu baglamini dogrudan bozuyoruz (manifest drift'i
    # simule eder) — seal_manifest'in kendisi FARKLI degerle YENIDEN
    # muhurlemeye izin vermez, o yuzden ORM nesnesini dogrudan degistiriyoruz.
    manifest = dict(run.execution_manifest or {})
    section = dict(manifest[MANIFEST_KEY])
    prompt_shas = dict(section["prompt_shas"])
    prompt_shas[STAGE_A1] = "baska-bir-prompt-sha"
    section["prompt_shas"] = prompt_shas
    manifest[MANIFEST_KEY] = section
    run.execution_manifest = manifest
    db_session.add(run)
    db_session.commit()

    fresh_ai = FakeFamilyAI(responses)
    with pytest.raises(StageContextMismatch):
        run_family_stage(
            db_session, run=run, profile=PROFILE, rows=rows, ai=fresh_ai,
            firm_block_sha256=_firm_sha(),
        )

    # Sessizce yeniden kullanim YOK: bozulan baglamla hicbir AI cagrisi
    # YAPILMAMIS olmali (fail-closed, DAHA OKUMADAN durur).
    assert fresh_ai.total_calls == 0
