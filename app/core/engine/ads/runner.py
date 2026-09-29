"""ADS Niche kosucusu — sinyaller + motor.

plan_algoritma_entegrasyonu.md Faz 3. Akis:

    funnel  -> Rel + huni + brand_type (batch 10, tek gecis)
    intent  -> Intent V4 ham puani; `apply_band` ile huni bandina KIRPILIR
    motor   -> rows (R, T, Rel, Intent, family) -> `run_niche_engine`

Girdiler DONMUS: evren `KeywordScore.metrics_snapshot`tan
(`context.load_universe`), aile Faz 2 ciktisindan (`family_by_id`) gelir.
Canli `workspace_keywords` OKUNMAZ, aile YENIDEN KURULMAZ.

Tekrar/butce sozlesmesi ortak `ai_runner` katmanindadir (K17); bu modul kendi
retry, checkpoint veya butce altyapisini KURMAZ. Saglayici disaridan enjekte
edilir.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.engine import ai_runner as AI
from app.core.engine.ads import engine as ENG
from app.core.engine.ads import formula as F
from app.core.engine.ads import prompts as P
from app.core.engine.context import EngineInputError
from app.core.engine.family.rules import UNMATCHED
from app.core.engine.persistence import (
    StageContext, load_stage_results, write_stage_results,
)
from app.database.models import ScoringRun

# Kanal basina model/thinking KOD SABITIDIR (plan K9); env EZEMEZ.
# Gemini 3.8 `minimal` kabul etmedigi icin tum uretim motorlari gibi `low`.
ADS_MODEL = "gemini-3.8-flash"
ADS_THINKING = "low"
ADS_ALGORITHM_VERSION = "nihai_niche_v2_gemini38"

FUNNEL_BATCH = 10
FUNNEL_MAX_TOKENS = 3000
INTENT_BATCH = 10
INTENT_MAX_TOKENS = 3000

SCOPE_KEYWORD = "keyword"
STAGE_FUNNEL = "ads_funnel"
STAGE_INTENT = "ads_intent"
ADS_STAGES = (STAGE_FUNNEL, STAGE_INTENT)


def ads_prompt_shas() -> Dict[str, str]:
    """Muhurlenecek prompt SHA'lari (asama -> sablon SHA'si).

    Funnel kimligi KILITLI sozlesmeden gelir (kanonik fixture render'inin
    SHA'si); uretim kendi urettigi bir hash'e baglanmaz. Intent kimligi
    sablonun kendi SHA'sidir (zincirin son halkasi V4).
    """
    return {STAGE_FUNNEL: P.FUNNEL_CANONICAL_RENDER_SHA256,
            STAGE_INTENT: P.INTENT_TEMPLATE_SHA256}


def ads_models() -> Dict[str, str]:
    return {stage: ADS_MODEL for stage in ADS_STAGES}


def _context(stage: str, firm_block_sha256: str) -> StageContext:
    return StageContext(model=ADS_MODEL, prompt_sha=ads_prompt_shas()[stage],
                        firm_block_sha256=firm_block_sha256)


def _rows_payload(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    return [{"keyword_id": row.keyword_id, "keyword_text": row.keyword_text}
            for row in rows]


def _signal_stage(db: Session, *, run: ScoringRun, stage: str,
                  rows: Sequence[Mapping[str, Any]], build_prompt,
                  schema: Mapping[str, Any], max_tokens: int, batch_size: int,
                  required_fields: Sequence[str], ai: Any,
                  firm_sha: str) -> Dict[int, Dict[str, Any]]:
    """Ortak sinyal asamasi: resume + batch + tek seferde kayit."""
    ctx = _context(stage, firm_sha)
    cached = load_stage_results(db, run=run, stage=stage,
                                scope_type=SCOPE_KEYWORD, context=ctx)
    out: Dict[int, Dict[str, Any]] = {int(k): dict(v) for k, v in cached.items()}
    pending = [row for row in rows if int(row["keyword_id"]) not in out]

    def _job(batch):
        def run():                       # WORKER: yalniz AI + dogrulama
            got = AI.run_batch(ai, stage=stage, model=ADS_MODEL,
                               thinking_level=ADS_THINKING, rows=batch,
                               build_prompt=build_prompt, schema=schema,
                               max_tokens=max_tokens, result_key="results")
            return {int(row["keyword_id"]): dict(got[int(row["keyword_id"])])
                    for row in batch}
        return run

    def _persist(entries):               # ANA THREAD: tek yazim noktasi
        write_stage_results(db, run=run, stage=stage,
                            scope_type=SCOPE_KEYWORD, entries=entries,
                            expected_keys=list(entries),
                            required_fields=tuple(required_fields),
                            context=ctx)
        out.update(entries)

    AI.run_jobs([_job(pending[i:i + batch_size])
                 for i in range(0, len(pending), batch_size)], _persist)
    return out


def validated_family_map(family_by_id: Mapping[Any, Any],
                        rows: Sequence[Any]) -> Dict[int, str]:
    """Faz 2 aile sonucunu motor girdisi olarak DOGRULAR — fail-closed.

    * Anahtarlar int'e normalize edilir (JSON'dan gelen string ID'ler kabul).
    * Anahtar kumesi, DONMUS evrenin keyword ID kumesiyle BIREBIR ayni olmali:
      eksik veya fazla ID kosuyu durdurur.
    * Bos/None aile ve `UNMATCHED` kabul EDILMEZ — kapanista her kelimenin
      bir ailesi olmasi gerekir (tekil aile dahil).

    Bu kontrol AI CAGRISINDAN ve stage yazimindan ONCE calisir: bozuk aile
    girdisiyle ucret harcanmaz ve yarim checkpoint olusmaz.
    """
    normalized: Dict[int, str] = {}
    for key, value in (family_by_id or {}).items():
        try:
            keyword_id = int(key)
        except (TypeError, ValueError):
            raise EngineInputError(
                f"aile sonucunda sayisal olmayan keyword id: {key!r}")
        if keyword_id in normalized:
            raise EngineInputError(
                f"aile sonucunda tekrarli keyword id: {keyword_id}")
        normalized[keyword_id] = value

    expected = {int(row.keyword_id) for row in rows}
    missing = sorted(expected - set(normalized))
    extra = sorted(set(normalized) - expected)
    if missing or extra:
        raise EngineInputError(
            "aile sonucu donmus evrenle ortusmuyor: "
            f"{len(missing)} eksik (ornek: {missing[:5]}), "
            f"{len(extra)} fazla (ornek: {extra[:5]})")

    bad = sorted(kid for kid, value in normalized.items()
                 if not isinstance(value, str) or not value.strip()
                 or value == UNMATCHED)
    if bad:
        raise EngineInputError(
            f"aile sonucunda gecersiz aile ({len(bad)} kelime, ornek: "
            f"{bad[:5]}): bos veya {UNMATCHED} — kapanista her kelimenin "
            "bir ailesi olmali")
    return {kid: str(value) for kid, value in normalized.items()}


def run_ads_stage(db: Session, *, run: ScoringRun,
                  profile: Mapping[str, Any], rows: Sequence[Any],
                  family_by_id: Mapping[int, str], ai: Any,
                  firm_block_sha256: str,
                  log: Optional[Callable[[str], None]] = None
                  ) -> Dict[str, Any]:
    """funnel -> intent -> Niche motoru. Havuz ve denetim ciktisini doner."""
    say = log or (lambda _msg: None)
    # AI CAGRISINDAN ONCE: aile sonucu donmus evrenle birebir ortusmeli.
    families = validated_family_map(family_by_id, rows)
    universe = _rows_payload(rows)

    funnel = _signal_stage(
        db, run=run, stage=STAGE_FUNNEL, rows=universe,
        build_prompt=lambda subset: P.build_funnel_prompt(profile, subset),
        schema=P.FUNNEL_SCHEMA, max_tokens=FUNNEL_MAX_TOKENS,
        batch_size=FUNNEL_BATCH,
        required_fields=("funnel", "brand_type", "relevance"),
        ai=ai, firm_sha=firm_block_sha256)
    say(f"funnel: {len(funnel)} kelime")

    intent = _signal_stage(
        db, run=run, stage=STAGE_INTENT, rows=universe,
        build_prompt=lambda subset: P.build_intent_prompt(profile, subset),
        schema=P.INTENT_SCHEMA, max_tokens=INTENT_MAX_TOKENS,
        batch_size=INTENT_BATCH, required_fields=("funnel", "intent"),
        ai=ai, firm_sha=firm_block_sha256)
    say(f"intent: {len(intent)} kelime")

    flags: Dict[str, int] = {}
    engine_rows: List[Dict[str, Any]] = []
    for row in rows:
        keyword_id = int(row.keyword_id)
        intent_item = intent.get(keyword_id) or {}
        # Intent ham puani DAIMA huni bandina kirpilir (kilitli davranis).
        intent_value, _clamped = P.apply_band(intent_item.get("funnel"),
                                              intent_item.get("intent"))
        engine_rows.append({
            "keyword_id": keyword_id,
            "keyword_text": row.keyword_text,
            "volume": row.volume,
            "R": F.competition_to_r(row.competition, flags),
            "T": F.trend_to_t(row.trend_3m, flags),
            "Rel": (funnel.get(keyword_id) or {}).get("relevance"),
            "Intent": intent_value,
            "family": families[keyword_id],
        })

    pool, checks = ENG.run_niche_engine(engine_rows)
    checks["donusum_bayraklari"] = flags
    say(f"motor: havuz {len(pool)} (kesim sonrasi)")
    return {"pool": pool, "checks": checks,
            "funnel": funnel, "intent": intent}
