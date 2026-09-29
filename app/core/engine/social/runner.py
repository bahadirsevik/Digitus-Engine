"""SOCIAL V5 kosucusu — tek gecisli sinyaller + saf zincir.

plan_algoritma_entegrasyonu.md Faz 5. Akis:

    relevance (batch 10) -> Rel >= 40 kapisi
      -> V4 dort boyut (batch 15) -> RF >= 50 ve BC >= 50 kapisi
      -> V5 niyet (batch 15) -> SocialScore -> Priority -> sira -> havuz

Girdi DONMUS evrendir (Faz 1 snapshot). SOCIAL AILE KURMAZ: Family V2 bu
kanalda CALISMAZ (plan K8).

Tekrar/butce ortak `ai_runner` (K17), kalicilik mevcut `persistence`;
YENI altyapi kurulmaz. Saglayici disaridan enjekte edilir.

TASINMAYANLAR (olcum mekanizmalari): uc tekrar, medyan/consensus, kontrol
capalari, patron etiketleri, Excel yazimi ve materialize.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.engine import ai_runner as AI
from app.core.engine.persistence import (
    StageContext, load_stage_results, write_stage_results,
)
from app.core.engine.seo import prompts as SEOP      # RELEVANCE sablonu ORTAK
from app.core.engine.social import pipeline as PL
from app.core.engine.social import prompts as P
from app.database.models import ScoringRun

# Kanal basina model/thinking KOD SABITIDIR (plan K9).
# SOCIAL: gemini-3.8-flash / low (SEO ile ayni, ADS'ten farkli).
SOCIAL_MODEL = "gemini-3.8-flash"
SOCIAL_THINKING = "low"

RELEVANCE_MAX_TOKENS = 3000

SCOPE_KEYWORD = "keyword"
STAGE_RELEVANCE = "social_rel"
STAGE_NORMBOUNDS = "social_normbounds"
STAGE_V4 = "social_v4"
STAGE_V5 = "social_intent"
SOCIAL_STAGES = (STAGE_RELEVANCE, STAGE_NORMBOUNDS, STAGE_V4, STAGE_V5)

NORMBOUNDS_MODEL = "deterministic"
NORMBOUNDS_CONTRACT_SHA256 = hashlib.sha256(
    b"social_v5_uretim_v1:normbounds:relevance_survivors:v1"
).hexdigest()


class SocialStageError(RuntimeError):
    """SOCIAL asamasi eksik/uyusmaz — yarim sonuc yazilmaz."""


def social_prompt_shas() -> Dict[str, str]:
    """Muhurlenecek prompt SHA'lari. Relevance SEO ile AYNI sablondur."""
    return {STAGE_RELEVANCE: SEOP.template_sha256("relevance"),
            STAGE_NORMBOUNDS: NORMBOUNDS_CONTRACT_SHA256,
            STAGE_V4: P.template_sha256(),
            STAGE_V5: P.V5_template_sha256()}


def social_models() -> Dict[str, str]:
    return {stage: (NORMBOUNDS_MODEL if stage == STAGE_NORMBOUNDS
                    else SOCIAL_MODEL) for stage in SOCIAL_STAGES}


def _context(stage: str, firm_block_sha256: str) -> StageContext:
    return StageContext(model=social_models()[stage],
                        prompt_sha=social_prompt_shas()[stage],
                        firm_block_sha256=firm_block_sha256)


def _parse_relevance_response(raw: Any,
                              expected_ids: Sequence[int]) -> Dict[str, Any]:
    """SEO relevance parser'ini ortak runner dogrulama sekline sarar."""
    from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_list

    expected = sorted({int(k) for k in expected_ids})
    expected_set = set(expected)
    base = {"unknown_ids": [], "duplicate_ids": [], "out_of_range_ids": [],
            "malformed_ids": []}
    try:
        items = parse_ai_json_list(raw, result_keys=("results",))
    except AIJsonParseError as exc:
        return {"ok": False, "parse_error": str(exc), "by_id": {},
                "missing_ids": expected, **base}

    seen: List[int] = []
    malformed: List[Any] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value = item.get("id")
        if isinstance(value, bool):
            malformed.append(value)
            continue
        try:
            seen.append(int(value))
        except (TypeError, ValueError):
            malformed.append(value)

    parsed = SEOP.parse_signal_results(
        "relevance", items, [{"keyword_id": kid} for kid in expected])
    by_id = {
        int(kid): {"id": int(kid), "relevance": rec["relevance"],
                   "band": rec.get("band"), "ham": rec.get("ham"),
                   "band_kirpildi": rec.get("band_kirpildi", False)}
        for kid, rec in parsed.items() if not rec.get("eksik")
    }
    missing = sorted(expected_set - set(by_id))
    unknown = sorted(set(seen) - expected_set)
    duplicates = sorted({kid for kid in seen if seen.count(kid) > 1})
    return {"ok": not (missing or unknown or duplicates or malformed),
            "parse_error": None, "by_id": by_id, "missing_ids": missing,
            "unknown_ids": unknown, "duplicate_ids": duplicates,
            "out_of_range_ids": [], "malformed_ids": malformed}


def _bounds_payload(survivors: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ids = sorted(int(row["keyword_id"]) for row in survivors)
    bounds = (PL.ENG.compute_norm_bounds(survivors).describe()
              if survivors else {})
    identity = json.dumps({"ids": ids, "bounds": bounds}, sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))
    return {"state": "ready" if survivors else "empty",
            "bounds": bounds,
            "survivor_identity_sha256": hashlib.sha256(
                identity.encode("utf-8")).hexdigest()}


def _freeze_normbounds(db: Session, *, run: ScoringRun,
                       survivors: Sequence[Mapping[str, Any]],
                       firm_sha: str) -> Optional[PL.ENG.NormBounds]:
    """NormBounds'i mevcut run-scope persistence ile bir kez dondurur."""
    ctx = _context(STAGE_NORMBOUNDS, firm_sha)
    expected = _bounds_payload(survivors)
    cached = load_stage_results(db, run=run, stage=STAGE_NORMBOUNDS,
                                scope_type="run", context=ctx)
    if cached:
        if set(cached) != {"norm_bounds"} or cached["norm_bounds"] != expected:
            raise SocialStageError(
                "social_normbounds: dondurulmus sinirlar yeni hesapla "
                "uyusmuyor — kosu DURDU")
    else:
        write_stage_results(
            db, run=run, stage=STAGE_NORMBOUNDS, scope_type="run",
            entries={"norm_bounds": expected}, expected_keys=["norm_bounds"],
            required_fields=("state", "bounds", "survivor_identity_sha256"),
            context=ctx)
    if expected["state"] == "empty":
        return None
    return PL.ENG.NormBounds(**expected["bounds"])


def _stage(db: Session, *, run: ScoringRun, stage: str,
           rows: Sequence[Mapping[str, Any]], build_prompt,
           schema: Mapping[str, Any], max_tokens: int, batch_size: int,
           required_fields: Sequence[str], ai: Any, firm_sha: str,
           result_key: Optional[str] = "results",
           response_id_field: str = "id", response_parser=None
           ) -> Dict[int, Dict[str, Any]]:
    """Kelime bazli asama: resume + batch + dogrulanmis tek seferde kayit.

    DIKKAT — iki AYRI cevap sozlesmesi vardir:
      * relevance: SEO ile ayni sablon -> `{"results": [{"id": ...}]}`
      * V4 / V5  : kilitli SOCIAL semalari UST DUZEY DIZI dondurur ve id
        alani `keyword_id`dir (`{"results": ...}` sarmali YOKTUR).
    Bu yuzden `result_key` / `response_id_field` asamaya gore verilir.
    """
    ctx = _context(stage, firm_sha)
    cached = load_stage_results(db, run=run, stage=stage,
                                scope_type=SCOPE_KEYWORD, context=ctx)
    out: Dict[int, Dict[str, Any]] = {int(k): dict(v) for k, v in cached.items()}
    pending = [row for row in rows if int(row["keyword_id"]) not in out]

    def _job(batch):
        def run():                       # WORKER: yalniz AI + dogrulama
            got = AI.run_batch(ai, stage=stage, model=SOCIAL_MODEL,
                               thinking_level=SOCIAL_THINKING, rows=batch,
                               build_prompt=build_prompt, schema=schema,
                               max_tokens=max_tokens, result_key=result_key,
                               response_id_field=response_id_field,
                               response_parser=response_parser,
                               error_cls=SocialStageError)
            return {int(row["keyword_id"]): dict(got[int(row["keyword_id"])])
                    for row in batch}
        return run

    def _persist(entries):               # ANA THREAD: tek yazim noktasi
        write_stage_results(db, run=run, stage=stage,
                            scope_type=SCOPE_KEYWORD, entries=entries,
                            expected_keys=list(entries),
                            required_fields=tuple(required_fields), context=ctx)
        out.update(entries)

    AI.run_jobs([_job(pending[i:i + batch_size])
                 for i in range(0, len(pending), batch_size)], _persist)

    missing = [int(r["keyword_id"]) for r in rows
               if int(r["keyword_id"]) not in out]
    if missing:
        raise SocialStageError(
            f"{stage}: {len(missing)} ID eksik (ornek: {missing[:5]})")
    return out


def run_social_stage(db: Session, *, run: ScoringRun,
                     profile: Mapping[str, Any], universe: Sequence[Any],
                     ai: Any, firm_block_sha256: str, firm_block_text: str,
                     pool_size: int = PL.DEFAULT_SOCIAL_POOL_SIZE,
                     log: Optional[Callable[[str], None]] = None
                     ) -> Dict[str, Any]:
    """relevance -> V4 -> V5 -> SocialScore/Priority/sira/havuz."""
    say = log or (lambda _msg: None)
    rows = [{"keyword_id": int(r.keyword_id), "keyword_text": r.keyword_text,
             "volume": r.volume, "trend_3m": r.trend_3m,
             "trend_12m": r.trend_12m} for r in universe]

    rel_raw = _stage(
        db, run=run, stage=STAGE_RELEVANCE, rows=rows,
        build_prompt=lambda subset: SEOP.build_relevance_prompt(profile, subset),
        schema=SEOP.REL_SCHEMA, max_tokens=RELEVANCE_MAX_TOKENS,
        batch_size=PL.BATCH["relevance"], required_fields=("id", "relevance"),
        ai=ai, firm_sha=firm_block_sha256,
        response_parser=_parse_relevance_response)
    # Relevance 0-1 SAKLANIR; motora x100 pipeline icinde girer.
    rel = {kid: {"relevance": item.get("relevance"), "band": item.get("band")}
           for kid, item in rel_raw.items()}
    say(f"relevance: {len(rel)} kelime")

    survivors = PL.v4_candidates(PL.relevance_rows(rows, rel))
    say(f"Rel >= {PL.ENG.RELEVANCE_GATE_MIN}: {len(survivors)}/{len(rows)}")
    bounds = _freeze_normbounds(
        db, run=run, survivors=survivors, firm_sha=firm_block_sha256)

    dims: Dict[int, Dict[str, Any]] = {}
    if survivors:
        dims = _stage(
            db, run=run, stage=STAGE_V4, rows=survivors,
            build_prompt=lambda subset: P.build_prompt(firm_block_text, subset),
            schema=P.RESPONSE_SCHEMA, max_tokens=P.MAX_TOKENS,
            batch_size=PL.BATCH["v4"],
            required_fields=("brand_contentability", "attention", "scenario",
                             "relative_fit"),
            ai=ai, firm_sha=firm_block_sha256,
            result_key=None, response_id_field="keyword_id",
            response_parser=P.parse_batch_response)

    eligible = PL.v5_candidates(survivors, dims)
    say(f"uc kapi: {len(eligible)}/{len(survivors)}")

    intents: Dict[int, Dict[str, Any]] = {}
    if eligible:
        intents = _stage(
            db, run=run, stage=STAGE_V5, rows=eligible,
            build_prompt=lambda subset: P.V5_build_prompt(firm_block_text, subset),
            schema=P.V5_RESPONSE_SCHEMA, max_tokens=P.V5_MAX_TOKENS,
            batch_size=PL.BATCH["v5"], required_fields=("social_intent_type",),
            ai=ai, firm_sha=firm_block_sha256,
            result_key=None, response_id_field="keyword_id",
            response_parser=P.V5_parse_batch_response)

    result = PL.compute_social_list(
        rows, rel, dims, intents, pool_size=pool_size, bounds=bounds)
    say(f"havuz: {len(result['pool'])} (kapasite {pool_size})")
    return {"result": result, "signals": {"relevance": rel, "v4": dims,
                                          "intent": intents}}
