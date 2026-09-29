"""Family V2 kosucusu — ADS ve SEO'nun ORTAK on kosulu (plan K8).

Akis (plan_nihai_ads_production.md §3.4 ve
algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md 5/5a/5b):

    A1   -> aile sozlugu (tek cagri)
    A2   -> batch atama (25'lik); YENI AILE ACAMAZ, uyum yoksa UNMATCHED
    A2B  -> butun UNMATCHED'ler TEK cagrida incelenir; yalniz YENI AILE onerir
            (kelime ATAMAZ). Destek >= 2 olan oneriler sozluge eklenir.
    ---- SOZLUK BURADA DONAR; A2C ve A3 sozlugu DEGISTIREMEZ ----
    A2C  -> butun UNMATCHED kumesi genisletilmis sozlukle YENIDEN atanir
    A3   -> ATANMIS fakat `confidence != "high"` satirlar 15'lik batch'lerde
            ikinci tura girer (UNMATCHED kelimeler A3'e GITMEZ). UNMATCHED
            hic olmasa bile dusuk guvenli atama varsa A3 KOSAR.
    son  -> hala UNMATCHED kalan kelime tekil aileye duser (`single:<id>`),
            0 uyeli aileler sozlukten duser

Kosucu evreni CANLI tablodan OKUMAZ: girdi, run'in dondurulmus evrenidir
(`context.load_universe`). Saglayici cagrisi disaridan ENJEKTE edilir; bu
modul kendi istemcisini KURMAZ, boylece testler ucretli cagri yapmadan
butun akisi kosabilir.

Asama sonuclari `engine_stage_results` icinde `scope_type="family"` ile
saklanir ve resume mühürlü baglamla dogrulanir (fail-closed).
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.engine import ai_runner as AI
from app.core.engine.family import prompts as P
from app.core.engine.family import rules as R
from app.core.engine.persistence import (
    StageContext, load_stage_results, write_stage_results,
)
from app.database.models import ScoringRun

# Kanal basina model/thinking KOD SABITIDIR (plan K9); env EZEMEZ.
# Gemini 3.8 `minimal` kabul etmedigi icin `low` kullanilir.
FAMILY_MODEL = "gemini-3.8-flash"
FAMILY_THINKING = "low"
FAMILY_ALGORITHM_VERSION = "nihai_family_v2_gemini38"

SCOPE_FAMILY = "family"
STAGE_A1 = "family_a1"
STAGE_A2 = "family_a2"
STAGE_A2B = "family_a2b"
STAGE_A2C = "family_a2c"
STAGE_A3 = "family_a3"
FAMILY_STAGES = (STAGE_A1, STAGE_A2, STAGE_A2B, STAGE_A2C, STAGE_A3)

DICTIONARY_KEY = "dictionary"
PROPOSALS_KEY = "proposals"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def family_prompt_shas() -> Dict[str, str]:
    """Muhurlenecek prompt SHA'lari (asama -> sablon SHA'si).

    A2C, A2 sablonunu genisletilmis sozlukle kullanir; ayni SHA'yi tasir.
    """
    contract = P.frozen_contract()
    return {
        STAGE_A1: contract["stage1_sha256"],
        STAGE_A2: contract["stage2_sha256"],
        STAGE_A2B: contract["stage2b_sha256"],
        STAGE_A2C: contract["stage2_sha256"],
        STAGE_A3: contract["stage3_sha256"],
    }


def family_models() -> Dict[str, str]:
    return {stage: FAMILY_MODEL for stage in FAMILY_STAGES}


def _context(run: ScoringRun, stage: str, firm_block_sha256: str) -> StageContext:
    return StageContext(model=FAMILY_MODEL,
                        prompt_sha=family_prompt_shas()[stage],
                        firm_block_sha256=firm_block_sha256)


def _rows_payload(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """UniverseRow -> prompt satiri (yalniz id + metin; metrik VERILMEZ)."""
    return [{"keyword_id": row.keyword_id, "keyword_text": row.keyword_text}
            for row in rows]


def run_family_stage(db: Session, *, run: ScoringRun,
                     profile: Mapping[str, Any],
                     rows: Sequence[Any], ai: Any,
                     firm_block_sha256: str,
                     log: Optional[Callable[[str], None]] = None
                     ) -> Dict[str, Any]:
    """A1 -> A2 -> A2B -> A2C -> A3 -> kapanis. Sonuc `family_by_id` tasir."""
    say = log or (lambda _msg: None)
    universe = _rows_payload(rows)
    by_id = {row["keyword_id"]: row for row in universe}

    families = _stage_a1(db, run, profile, universe, ai, firm_block_sha256, say)
    assigned = _stage_a2(db, run, profile, families, universe, ai,
                         firm_block_sha256, say)

    unmatched = [by_id[kid] for kid in sorted(by_id)
                 if assigned.get(kid, {}).get("family_id", R.UNMATCHED)
                 == R.UNMATCHED]
    R.assert_unmatched_ceiling(len(unmatched))
    say(f"A2: UNMATCHED {len(unmatched)}/{len(universe)}")

    a2b_payload: Dict[str, Any] = {}
    if unmatched:
        families, a2b_payload = _stage_a2b(
            db, run, profile, families, unmatched, ai, firm_block_sha256, say)
        # ---- SOZLUK BURADA DONAR ----
        assigned.update(_stage_a2c(db, run, profile, families, unmatched, ai,
                                   firm_block_sha256, say))
    frozen_sha = R.dict_sha(families)

    # A3: DUSUK GUVEN ikinci turu (kilitli davranis).
    # Secim TUM EVRENDEN yapilir: `confidence != "high"` VE atanmis
    # (`family_id != UNMATCHED`). UNMATCHED kelimeler A3'e GITMEZ; onlar
    # kapanista `single:<keyword_id>` olur. UNMATCHED hic olmasa bile dusuk
    # guvenli atama varsa A3 KOSAR.
    low = [by_id[kid] for kid in sorted(by_id)
           if assigned.get(kid, {}).get("confidence") != "high"
           and assigned.get(kid, {}).get("family_id", R.UNMATCHED) != R.UNMATCHED]
    if low:
        say(f"A3: {len(low)} kararsiz atama ikinci tura giriyor")
        assigned.update(_stage_a3(db, run, profile, families, low, assigned,
                                  ai, firm_block_sha256, say))
    if R.dict_sha(families) != frozen_sha:                 # invariant
        raise R.FamilyStageError("A2C/A3 sozlugu degistirdi — kosu iptal")

    result = R.finalize_assignments(
        {kid: item["family_id"] for kid, item in assigned.items()},
        families, universe_ids=[r["keyword_id"] for r in universe])
    result["a2b"] = a2b_payload
    result["unmatched_after_a2"] = len(unmatched)
    result["low_confidence_reviewed"] = len(low)
    say(f"aile: {len(result['families'])} aile, "
        f"{len(result['single_family_keyword_ids'])} tekil")
    return result


# ── asamalar ─────────────────────────────────────────────────────────

def _stage_a1(db, run, profile, universe, ai, firm_sha, say) -> List[Dict[str, Any]]:
    ctx = _context(run, STAGE_A1, firm_sha)
    cached = load_stage_results(db, run=run, stage=STAGE_A1,
                                scope_type=SCOPE_FAMILY, context=ctx)
    if DICTIONARY_KEY in cached:
        families = cached[DICTIONARY_KEY].get("families") or []
        say(f"A1: {len(families)} aile (resume)")
        return [dict(f) for f in families]

    families = AI.run_single(
        ai, stage=STAGE_A1, model=FAMILY_MODEL, thinking_level=FAMILY_THINKING,
        prompt=P.build_stage1(profile, universe), schema=P.STAGE1_SCHEMA,
        max_tokens=R.STAGE1_MAX_TOKENS, result_key="families",
        error_cls=R.FamilyStageError)
    problems = R.validate_families(families)
    if problems:
        raise R.FamilyStageError(f"A1 dogrulamasi DUSTU -> {problems}")

    write_stage_results(
        db, run=run, stage=STAGE_A1, scope_type=SCOPE_FAMILY,
        entries={DICTIONARY_KEY: {"families": families,
                                  "dictionary_sha256": R.dict_sha(families)}},
        expected_keys=[DICTIONARY_KEY],
        required_fields=("families", "dictionary_sha256"), context=ctx)
    say(f"A1: {len(families)} aile uretildi")
    return [dict(f) for f in families]


def _assign_batches(db, run, profile, families, rows, ai, firm_sha, stage,
                    say) -> Dict[int, Dict[str, Any]]:
    """A2 / A2C ortak batch atamasi (25'lik).

    `confidence` KORUNUR: A3 secimi buna dayanir. Sozluk disi bir
    `family_id` GECERSIZDIR ve UNMATCHED sayilir (kilitli davranis).
    """
    ctx = _context(run, stage, firm_sha)
    cached = load_stage_results(db, run=run, stage=stage,
                                scope_type=SCOPE_FAMILY, context=ctx)
    out: Dict[int, Dict[str, Any]] = {
        int(k): {"family_id": v["family_id"],
                 "confidence": v.get("confidence") or "low"}
        for k, v in cached.items()}
    valid = {f["family_id"] for f in families}
    pending = [row for row in rows if row["keyword_id"] not in out]

    def _job(batch):
        def run():                       # WORKER: yalniz AI + dogrulama
            got = AI.run_batch(
                ai, stage=stage, model=FAMILY_MODEL,
                thinking_level=FAMILY_THINKING, rows=batch,
                build_prompt=lambda subset: P.build_stage2(profile, families,
                                                           subset),
                schema=P.STAGE2_SCHEMA, max_tokens=R.STAGE2_MAX_TOKENS,
                result_key="results", error_cls=R.FamilyStageError)
            entries = {}
            for row in batch:
                item = got[row["keyword_id"]]
                fid = item.get("family_id")
                if fid not in valid:      # sozluk disi family_id GECERSIZ
                    fid = R.UNMATCHED
                entries[row["keyword_id"]] = {
                    "family_id": fid,
                    "confidence": item.get("confidence") or "low",
                    "stage": stage,
                }
            return entries
        return run

    def _persist(entries):               # ANA THREAD: tek yazim noktasi
        write_stage_results(
            db, run=run, stage=stage, scope_type=SCOPE_FAMILY, entries=entries,
            expected_keys=list(entries),
            required_fields=("family_id", "confidence"), context=ctx)
        out.update({kid: {"family_id": v["family_id"],
                          "confidence": v["confidence"]}
                    for kid, v in entries.items()})

    AI.run_jobs([_job(pending[i:i + R.ASSIGN_BATCH])
                 for i in range(0, len(pending), R.ASSIGN_BATCH)], _persist)
    return out


def _stage_a2(db, run, profile, families, universe, ai, firm_sha, say):
    return _assign_batches(db, run, profile, families, universe, ai, firm_sha,
                           STAGE_A2, say)


def _stage_a2c(db, run, profile, families, unmatched, ai, firm_sha, say):
    return _assign_batches(db, run, profile, families, unmatched, ai, firm_sha,
                           STAGE_A2C, say)


def _stage_a2b(db, run, profile, families, unmatched, ai, firm_sha, say):
    """Yalniz YENI AILE onerir; kelime ATAMAZ. Destek >= 2 olanlar kabul."""
    ctx = _context(run, STAGE_A2B, firm_sha)
    cached = load_stage_results(db, run=run, stage=STAGE_A2B,
                                scope_type=SCOPE_FAMILY, context=ctx)
    if PROPOSALS_KEY in cached:
        payload = cached[PROPOSALS_KEY]
        accepted = payload.get("accepted_families") or []
        say(f"A2B: {len(accepted)} yeni aile (resume)")
        return ([dict(f) for f in families] + [dict(f) for f in accepted],
                payload)

    proposed = AI.run_single(
        ai, stage=STAGE_A2B, model=FAMILY_MODEL,
        thinking_level=FAMILY_THINKING,
        prompt=P.build_stage2b(profile, families, unmatched),
        schema=P.STAGE2B_SCHEMA, max_tokens=R.STAGE2B_MAX_TOKENS,
        result_key="new_families", error_cls=R.FamilyStageError)

    existing_ids = [f["family_id"] for f in families]
    outcome = R.evaluate_a2b_proposals(proposed, unmatched,
                                       existing_family_ids=existing_ids)
    if outcome.families:
        problems = R.validate_families(outcome.families, existing=existing_ids)
        if problems:
            raise R.FamilyStageError(f"A2B dogrulamasi DUSTU -> {problems}")

    merged = [dict(f) for f in families] + [dict(f) for f in outcome.families]
    payload = dict(outcome.as_payload())
    payload["accepted_families"] = [dict(f) for f in outcome.families]
    payload["dictionary_sha256_frozen"] = R.dict_sha(merged)

    write_stage_results(
        db, run=run, stage=STAGE_A2B, scope_type=SCOPE_FAMILY,
        entries={PROPOSALS_KEY: payload}, expected_keys=[PROPOSALS_KEY],
        required_fields=("min_support", "dictionary_sha256_frozen"),
        context=ctx)
    say(f"A2B: {len(outcome.families)} yeni aile kabul, "
        f"{len(outcome.rejected)} red")
    return merged, payload


def a3_candidates(current: str, families: Sequence[Mapping[str, Any]]
                  ) -> List[str]:
    """A3 aday listesi — kilitli davranis.

    Mevcut aile + AYNI `solution_type` icindeki en fazla UC aile;
    tekillestirilip EN FAZLA DORT ogeye kirpilir. Butun sozluk GONDERILMEZ.
    Bicim: "family_id | family_name".
    """
    by_id = {f["family_id"]: f for f in families}
    solution = (by_id.get(current) or {}).get("solution_type")
    same_solution = [f["family_id"] for f in families
                     if f.get("solution_type") == solution][:3]
    ordered = list(dict.fromkeys([current] + same_solution))[:4]
    return [f"{fid} | {by_id[fid]['family_name']}"
            for fid in ordered if fid in by_id]


def _stage_a3(db, run, profile, families, low, assigned, ai, firm_sha, say):
    """Ikinci tur: yalniz family_id degistirebilir, YENI AILE OLUSTURAMAZ."""
    ctx = _context(run, STAGE_A3, firm_sha)
    cached = load_stage_results(db, run=run, stage=STAGE_A3,
                                scope_type=SCOPE_FAMILY, context=ctx)
    out: Dict[int, Dict[str, Any]] = {
        int(k): {"family_id": v["family_id"],
                 "confidence": assigned.get(int(k), {}).get("confidence",
                                                            "low")}
        for k, v in cached.items()}
    valid = {f["family_id"] for f in families}
    pending = [row for row in low if row["keyword_id"] not in out]

    def _build_for(batch):
        def build(subset):
            records = []
            for row in subset:
                current = assigned[row["keyword_id"]]["family_id"]
                records.append({
                    "keyword_id": row["keyword_id"],
                    "keyword_text": row["keyword_text"],
                    "first_pass": current,
                    "confidence": assigned[row["keyword_id"]]["confidence"],
                    "candidates": a3_candidates(current, families)})
            return P.build_stage3(profile, records)

        return build

    def _job(batch):
        def run():                       # WORKER: yalniz AI + dogrulama
            got = AI.run_batch(
                ai, stage=STAGE_A3, model=FAMILY_MODEL,
                thinking_level=FAMILY_THINKING, rows=batch,
                build_prompt=_build_for(batch),
                schema=P.STAGE3_SCHEMA, max_tokens=R.STAGE3_MAX_TOKENS,
                result_key="results", error_cls=R.FamilyStageError)
            entries = {}
            for row in batch:
                fid = got[row["keyword_id"]].get("family_id")
                if fid not in valid:      # A3 YENI AILE ACAMAZ
                    fid = assigned[row["keyword_id"]]["family_id"]
                entries[row["keyword_id"]] = {"family_id": fid,
                                              "stage": STAGE_A3}
            return entries
        return run

    def _persist(entries):               # ANA THREAD: tek yazim noktasi
        write_stage_results(
            db, run=run, stage=STAGE_A3, scope_type=SCOPE_FAMILY,
            entries=entries, expected_keys=list(entries),
            required_fields=("family_id",), context=ctx)
        out.update({kid: {"family_id": v["family_id"],
                          "confidence": assigned[kid]["confidence"]}
                    for kid, v in entries.items()})

    AI.run_jobs([_job(pending[i:i + R.STAGE3_BATCH])
                 for i in range(0, len(pending), R.STAGE3_BATCH)], _persist)
    return out
