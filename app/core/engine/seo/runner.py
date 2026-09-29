"""SEO kati-2 kosucusu — sinyaller + iki gecisli secim.

plan_algoritma_entegrasyonu.md Faz 4. Akis ve SIRA BAGLAYICIDIR:

    rel -> bp (yalniz Rel >= 0,50) -> sub-intent (aile basina tek cagri)
        -> authority (yalniz Rel >= 0,85 ve 0,70 <= BP < 0,80 penceresi)
        -> 1. GECIS (adaylar) -> URL grubu (>= 2 adayi olan aile basina tek cagri)
        -> KAPI -> 2. GECIS (gruplarla gercek secim, family_cap=2)

Girdiler DONMUS: evren Faz 1 snapshot'indan, aile Faz 2 ciktisindan.
Tekrar/butce ortak `ai_runner` (K17), kalicilik mevcut `persistence`;
YENI altyapi kurulmaz. Saglayici disaridan enjekte edilir.

KAPI (ikinci gecis on kosulu): authority ve URL grubu sonuclari AYNI
`scoring_run`a ve MUHURLU manifest baglamina ait olmali; uretilmesi gereken
ID kumelerinde eksik varsa kosu DURUR ve secim YAZILMAZ.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.engine import ai_runner as AI
from app.core.engine.context import EngineInputError
from app.core.engine.persistence import (
    StageContext, load_stage_results, write_stage_results,
)
from app.core.engine.seo import prompts as P
from app.core.engine.seo.rows import build_rows
from app.core.engine.seo.select import (
    authority_window_ids, evaluate, production_contract,
)
from app.core.engine.seo.urlgroup import run_two_pass
from app.database.models import ScoringRun

# Kanal basina model/thinking KOD SABITIDIR (plan K9). SEO: gemini-3.8-flash /
# low — tum aktif uretim motorlari Gemini 3.8 / low kullanir.
SEO_MODEL = P.MODEL
SEO_THINKING = P.THINKING_LEVEL

SIGNAL_BATCH = 10
REL_MAX_TOKENS = 3000
BP_MAX_TOKENS = 3000
SUBINTENT_MAX_TOKENS = 20000
AUTHORITY_MAX_TOKENS = 3000
URLGROUP_MAX_TOKENS = 8000

BP_RELEVANCE_FLOOR = 0.50          # BP evreni (kilitli sozlesme)

STAGE_REL = "seo_rel"
STAGE_BP = "seo_bp"
STAGE_SUBINTENT = "seo_subintent"
STAGE_AUTHORITY = "seo_authority"
STAGE_URLGROUP = "seo_urlgroup"
SEO_STAGES = (STAGE_REL, STAGE_BP, STAGE_SUBINTENT, STAGE_AUTHORITY,
              STAGE_URLGROUP)

SCOPE_KEYWORD = "keyword"
SCOPE_FAMILY = "family"
SCOPE_URL_GROUP = "url_group"
SCOPE_BY_STAGE = {STAGE_REL: SCOPE_KEYWORD, STAGE_BP: SCOPE_KEYWORD,
                  STAGE_AUTHORITY: SCOPE_KEYWORD,
                  STAGE_SUBINTENT: SCOPE_FAMILY,
                  STAGE_URLGROUP: SCOPE_URL_GROUP}


class SeoStageError(RuntimeError):
    """SEO asamasi eksik/uyusmaz — ikinci gecis baslamaz, secim YAZILMAZ."""


def seo_prompt_shas() -> Dict[str, str]:
    return {STAGE_REL: P.template_sha256("relevance"),
            STAGE_BP: P.template_sha256("bp"),
            STAGE_SUBINTENT: P.template_sha256("subintent"),
            STAGE_AUTHORITY: P.V31_template_sha256("authority"),
            STAGE_URLGROUP: P.V31_template_sha256("urlgroup")}


def seo_models() -> Dict[str, str]:
    return {stage: SEO_MODEL for stage in SEO_STAGES}


def _context(stage: str, firm_block_sha256: str) -> StageContext:
    return StageContext(model=SEO_MODEL, prompt_sha=seo_prompt_shas()[stage],
                        firm_block_sha256=firm_block_sha256)


def assert_complete(stage: str, expected_ids: Sequence[Any],
                    produced: Mapping[Any, Any]) -> None:
    """Uretilmesi GEREKEN ID kumesi eksiksiz mi — fail-closed."""
    missing = [key for key in expected_ids if key not in produced]
    if missing:
        raise SeoStageError(
            f"{stage}: uretilmesi gereken {len(missing)} ID eksik "
            f"(ornek: {list(missing)[:5]}) — ikinci gecis baslamaz, "
            "secim yazilmaz")


def _keyword_stage(db: Session, *, run: ScoringRun, stage: str, kind: str,
                   batch_rows: Sequence[Mapping[str, Any]], build_prompt,
                   schema: Mapping[str, Any], max_tokens: int,
                   required_fields: Sequence[str], ai: Any,
                   firm_sha: str) -> Dict[int, Dict[str, Any]]:
    """Kelime bazli sinyal asamasi: resume + batch + tek seferde kayit.

    DIKKAT: ham AI degeri DOGRUDAN kullanilmaz. `relevance` ve `bp` icin
    kilitli `parse_signal_results` calisir — deger BANDINA KIRPILIR
    (`apply_band`) ve eksik cevap `eksik=True` ile isaretlenir. Eksik satir
    fail-closed: kosu durur.
    """
    ctx = _context(stage, firm_sha)
    cached = load_stage_results(db, run=run, stage=stage,
                                scope_type=SCOPE_KEYWORD, context=ctx)
    out: Dict[int, Dict[str, Any]] = {int(k): dict(v) for k, v in cached.items()}
    pending = [row for row in batch_rows if int(row["keyword_id"]) not in out]

    def _job(batch):
        def run():                       # WORKER: yalniz AI + dogrulama
            got = AI.run_batch(ai, stage=stage, model=SEO_MODEL,
                               thinking_level=SEO_THINKING, rows=batch,
                               build_prompt=build_prompt, schema=schema,
                               max_tokens=max_tokens, result_key="results",
                               error_cls=SeoStageError)
            if kind == "authority":
                parsed = {int(row["keyword_id"]):
                          dict(got[int(row["keyword_id"])]) for row in batch}
            else:
                items = [got[int(row["keyword_id"])] for row in batch]
                parsed = P.parse_signal_results(kind, items, batch)
            missing = sorted(kid for kid, rec in parsed.items()
                             if rec.get("eksik"))
            if missing:
                raise SeoStageError(
                    f"{stage}: {len(missing)} kelimede sinyal EKSIK "
                    f"(ornek: {missing[:5]}) — yarim sonuc yazilmaz")
            return {int(kid): dict(rec) for kid, rec in parsed.items()}
        return run

    def _persist(entries):               # ANA THREAD: tek yazim noktasi
        write_stage_results(db, run=run, stage=stage,
                            scope_type=SCOPE_KEYWORD, entries=entries,
                            expected_keys=list(entries),
                            required_fields=tuple(required_fields), context=ctx)
        out.update(entries)

    AI.run_jobs([_job(pending[i:i + SIGNAL_BATCH])
                 for i in range(0, len(pending), SIGNAL_BATCH)], _persist)
    assert_complete(stage, [int(r["keyword_id"]) for r in batch_rows], out)
    return out


def _ids_in_items(items: Sequence[Any], id_field: str) -> List[int]:
    """Cevaptaki ID'ler. `id_field="id"` (alt niyet) veya `"ids"` (URL grubu)."""
    seen: List[int] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        raw = item.get(id_field)
        values = raw if id_field == "ids" else [raw]
        for value in (values or []):
            try:
                seen.append(int(value))
            except (TypeError, ValueError):
                raise SeoStageError(
                    f"cevapta sayisal olmayan id: {value!r} — yapisal hata")
    return seen


def _ask_unit(ai: Any, *, stage: str, unit: Mapping[str, Any],
              ids: Sequence[int], schema: Mapping[str, Any], max_tokens: int,
              result_key: str, id_field: str) -> List[Any]:
    """Tek birim cagrisi + ID dogrulamasi (checkpoint YAZMADAN)."""
    items = AI.run_single(ai, stage=stage, model=SEO_MODEL,
                          thinking_level=SEO_THINKING,
                          prompt=unit["build_prompt"](list(ids)), schema=schema,
                          max_tokens=max_tokens, result_key=result_key,
                          error_cls=SeoStageError)
    seen = set(_ids_in_items(items, id_field))
    unexpected = sorted(seen - set(ids))
    if unexpected:
        # Fazladan / yabanci ID: sessizce yok SAYILMAZ, checkpoint YAZILMAZ.
        raise SeoStageError(
            f"{stage}/{unit['scope_key']}: cevapta istenmeyen id "
            f"{unexpected[:5]} — yapisal hata, checkpoint yazilmaz")
    return items


def _unit_stage(db: Session, *, run: ScoringRun, stage: str, scope_type: str,
                units: Sequence[Mapping[str, Any]], schema: Mapping[str, Any],
                max_tokens: int, result_key: str, id_field: str, ai: Any,
                firm_sha: str) -> Dict[str, Dict[str, Any]]:
    """Birim (aile / URL grubu) bazli asama — K17 sozlesmesiyle.

    Cevap, checkpoint'e YAZILMADAN ONCE beklenen ID kumesine karsi dogrulanir:
      * yabanci/fazladan ID  -> yapisal hata, satir YAZILMAZ,
      * eksik ID             -> YALNIZ eksik kume icin 1 hedefli tekrar,
      * tekrardan sonra hala eksik -> `SeoStageError`, satir YAZILMAZ.

    Daha once BASARIYLA tamamlanmis birimlerin checkpoint'leri korunur; yalniz
    basarisiz/kismi birim yazilmaz.
    """
    ctx = _context(stage, firm_sha)
    cached = load_stage_results(db, run=run, stage=stage,
                                scope_type=scope_type, context=ctx)
    out: Dict[str, Dict[str, Any]] = {str(k): dict(v) for k, v in cached.items()}

    def _job(unit):
        key = str(unit["scope_key"])

        def run():                       # WORKER: cagri + hedefli tekrar
            expected = [int(k) for k in unit["ids"]]
            items = _ask_unit(ai, stage=stage, unit=unit, ids=expected,
                              schema=schema, max_tokens=max_tokens,
                              result_key=result_key, id_field=id_field)
            missing = [kid for kid in expected
                       if kid not in set(_ids_in_items(items, id_field))]
            for _ in range(AI.TARGETED_RETRIES):
                if not missing:
                    break
                # YALNIZ eksik kume tekrar sorulur (hedefli tekrar).
                extra = _ask_unit(ai, stage=stage, unit=unit, ids=missing,
                                  schema=schema, max_tokens=max_tokens,
                                  result_key=result_key, id_field=id_field)
                items = list(items) + list(extra)
                missing = [kid for kid in expected
                           if kid not in set(_ids_in_items(items, id_field))]
            if missing:
                raise SeoStageError(
                    f"{stage}/{key}: hedefli tekrara ragmen {len(missing)} ID "
                    f"eksik (ornek: {missing[:5]}) — checkpoint OLUSTURULMAZ")
            return key, {"items": items, "ids": expected}
        return run

    def _persist(result):                # ANA THREAD: tek yazim noktasi
        key, payload = result
        write_stage_results(db, run=run, stage=stage, scope_type=scope_type,
                            entries={key: payload}, expected_keys=[key],
                            required_fields=("items", "ids"), context=ctx)
        out[key] = payload

    AI.run_jobs([_job(u) for u in units if str(u["scope_key"]) not in out],
                _persist)
    assert_complete(stage, [str(u["scope_key"]) for u in units], out)
    return out


def run_seo_stage(db: Session, *, run: ScoringRun,
                  profile: Mapping[str, Any], universe: Sequence[Any],
                  family_by_id: Mapping[int, str],
                  families: Sequence[Mapping[str, Any]], ai: Any,
                  firm_block_sha256: str,
                  log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Sinyaller -> 1. gecis -> URL grubu -> KAPI -> 2. gecis."""
    say = log or (lambda _msg: None)
    contract = production_contract()
    batch_rows = [{"keyword_id": int(r.keyword_id), "keyword_text": r.keyword_text}
                  for r in universe]
    by_kid = {int(r.keyword_id): r for r in universe}

    rel = _keyword_stage(
        db, run=run, stage=STAGE_REL, kind="relevance",
        batch_rows=batch_rows,
        build_prompt=lambda subset: P.build_relevance_prompt(profile, subset),
        schema=P.REL_SCHEMA, max_tokens=REL_MAX_TOKENS,
        required_fields=("id", "relevance"), ai=ai, firm_sha=firm_block_sha256)
    say(f"rel: {len(rel)} kelime")

    # BP evreni: YALNIZ Rel >= 0,50
    bp_rows = [row for row in batch_rows
               if float((rel[int(row["keyword_id"])] or {}).get("relevance") or 0.0)
               >= BP_RELEVANCE_FLOOR]
    bp = _keyword_stage(
        db, run=run, stage=STAGE_BP, kind="bp", batch_rows=bp_rows,
        build_prompt=lambda subset: P.build_bp_prompt(profile, subset),
        schema=P.BP_SCHEMA, max_tokens=BP_MAX_TOKENS,
        required_fields=("id", "business_proximity"), ai=ai,
        firm_sha=firm_block_sha256) if bp_rows else {}
    say(f"bp: {len(bp)}/{len(batch_rows)} (Rel >= {BP_RELEVANCE_FLOOR})")

    # Alt niyet: AILE basina TEK cagri
    members: Dict[str, List[int]] = {}
    for keyword_id, family_id in family_by_id.items():
        members.setdefault(str(family_id), []).append(int(keyword_id))
    family_meta = {str(f["family_id"]): f for f in families}
    def _subintent_prompt(fid: str):
        def build(ids: Sequence[int]) -> str:
            return P.build_subintent_prompt(
                profile, family_meta.get(fid) or {"family_id": fid},
                [{"keyword_id": kid, "keyword_text": by_kid[kid].keyword_text}
                 for kid in sorted(ids)])
        return build

    sub_units = [{"scope_key": fid, "ids": sorted(ids),
                  "build_prompt": _subintent_prompt(fid)}
                 for fid, ids in sorted(members.items())]
    sub_raw = _unit_stage(db, run=run, stage=STAGE_SUBINTENT,
                          scope_type=SCOPE_FAMILY, units=sub_units,
                          schema=P.SUBINTENT_SCHEMA,
                          max_tokens=SUBINTENT_MAX_TOKENS,
                          result_key="results", id_field="id",
                          ai=ai, firm_sha=firm_block_sha256)
    subintent = _flatten_subintent(sub_raw, by_kid)
    assert_complete(STAGE_SUBINTENT, [int(r["keyword_id"]) for r in batch_rows],
                    subintent)

    rows = build_rows(universe, family_by_id=family_by_id, subintent=subintent,
                      relevance=rel, bp=bp)

    # Authority: YALNIZ dar pencere
    window = authority_window_ids(rows, contract)
    authority: Dict[int, Dict[str, Any]] = {}
    if window:
        auth_rows = [{"keyword_id": kid,
                      "keyword_text": by_kid[kid].keyword_text} for kid in window]
        authority = _keyword_stage(
            db, run=run, stage=STAGE_AUTHORITY, kind="authority",
            batch_rows=auth_rows,
            build_prompt=lambda subset: P.build_authority_prompt(
                profile, [{"id": r["keyword_id"], "keyword": r["keyword_text"]}
                          for r in subset]),
            schema=P.AUTHORITY_SCHEMA, max_tokens=AUTHORITY_MAX_TOKENS,
            required_fields=("authority",), ai=ai, firm_sha=firm_block_sha256)
    say(f"authority: {len(authority)}/{len(window)} (pencere)")

    rows = build_rows(universe, family_by_id=family_by_id, subintent=subintent,
                      relevance=rel, bp=bp, authority=authority)

    # 1. GECIS: adaylar (gruplar aday kimligine baglidir)
    first = evaluate(rows, contract, with_secondary=False)
    by_id = {int(r["keyword_id"]): r for r in rows}
    per_family: Dict[str, List[Any]] = {}
    for cluster, keyword_id in first["primary_by_cluster"].items():
        per_family.setdefault(str(by_id[int(keyword_id)]["family_id"]), []).append(
            (cluster, int(keyword_id)))

    url_units = []
    for fid in sorted((f for f, v in per_family.items() if len(v) >= 2), key=str):
        adaylar = sorted(per_family[fid])
        cands = [{"id": kid, "keyword": by_id[kid]["keyword_text"],
                  "subintent_id": by_id[kid]["subintent_id"],
                  "subintent_label": by_id[kid].get("subintent_label")}
                 for _cl, kid in adaylar]
        def _urlgroup_prompt(fid=fid, cands=cands):
            by_cand = {int(c["id"]): c for c in cands}

            def build(ids: Sequence[int]) -> str:
                return P.build_urlgroup_prompt(
                    profile, family_meta.get(fid) or {"family_id": fid},
                    [by_cand[int(kid)] for kid in ids if int(kid) in by_cand])
            return build

        url_units.append({"scope_key": fid, "ids": [kid for _cl, kid in adaylar],
                          "build_prompt": _urlgroup_prompt()})

    url_raw = _unit_stage(db, run=run, stage=STAGE_URLGROUP,
                          scope_type=SCOPE_URL_GROUP, units=url_units,
                          schema=P.URLGROUP_SCHEMA,
                          max_tokens=URLGROUP_MAX_TOKENS,
                          result_key="groups", id_field="ids",
                          ai=ai, firm_sha=firm_block_sha256) if url_units else {}
    keyword_to_group = _flatten_urlgroups(url_raw)
    # KAPI: her birimin uretmesi gereken ID'ler eksiksiz mi?
    for unit in url_units:
        assert_complete(f"{STAGE_URLGROUP}/{unit['scope_key']}", unit["ids"],
                        keyword_to_group)
    say(f"url grubu: {len(url_units)} aile, {len(keyword_to_group)} kelime")

    # 2. GECIS: gruplarla gercek secim
    main = run_two_pass(rows, keyword_to_group, contract=contract)
    return {"selection": main, "rows": rows, "contract": contract.describe()
            if hasattr(contract, "describe") else None,
            "signals": {"relevance": rel, "bp": bp, "subintent": subintent,
                        "authority": authority,
                        "url_groups": keyword_to_group}}


def _flatten_subintent(raw: Mapping[str, Mapping[str, Any]],
                      by_kid: Mapping[int, Any]) -> Dict[int, Dict[str, Any]]:
    """Aile ciktilarini kelime kayitlarina indirger — KILITLI parse ile.

    `parse_signal_results("subintent", ...)` gecerli `broad_intent` ve
    `subintent_id` yoksa kaydi `eksik=True` isaretler; eksik satir fail-closed.
    """
    out: Dict[int, Dict[str, Any]] = {}
    for payload in raw.values():
        ids = [int(k) for k in (payload.get("ids") or [])]
        batch = [{"keyword_id": kid,
                  "keyword_text": getattr(by_kid.get(kid), "keyword_text", "")}
                 for kid in ids]
        parsed = P.parse_signal_results("subintent",
                                        list(payload.get("items") or []), batch)
        missing = sorted(kid for kid, rec in parsed.items() if rec.get("eksik"))
        if missing:
            raise SeoStageError(
                f"{STAGE_SUBINTENT}: {len(missing)} kelimede alt niyet EKSIK "
                f"(ornek: {missing[:5]})")
        out.update({int(kid): dict(rec) for kid, rec in parsed.items()})
    return out


def _flatten_urlgroups(raw: Mapping[str, Mapping[str, Any]]) -> Dict[int, str]:
    """KILITLI `parse_urlgroups`: bir aday birden fazla grupta gorunurse ILK
    grubu gecerlidir (deterministik)."""
    out: Dict[int, str] = {}
    for payload in raw.values():
        ids = [int(k) for k in (payload.get("ids") or [])]
        mapped, missing = P.parse_urlgroups(list(payload.get("items") or []), ids)
        if missing:
            raise SeoStageError(
                f"{STAGE_URLGROUP}: {len(missing)} aday gruba atanmadi "
                f"(ornek: {missing[:5]}) — ikinci gecis baslamaz")
        for kid, gid in mapped.items():
            out.setdefault(int(kid), str(gid))      # ILK grup gecerli
    return out
