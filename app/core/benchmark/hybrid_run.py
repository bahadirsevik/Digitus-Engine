"""Faz 1 / 1B — uçtan uca hibrit konfigürasyon koşucusu (plan §5 Faz 1, §6).

Konfigürasyonlar (rota tablosu mühürlü plandan türetilir, model kimliği
KODDA YOK): `G/G/G` (hepsi Gemini), `Aday` (Y→Haiku, O→Sonnet, içerik→Sonnet),
`G/G/C` (yalnız içerik→Sonnet). Her koşu AYRI bir v3 ScoringRun'dır (aynı
workspace → aynı dondurulmuş evren; parmak izi sonuçta).

Güvenlik sırası (Codex kabul ölçütleri):
  1. `faz1_blockers(plan)` boş değilse `Faz1Blocked` — HİÇBİR AI nesnesi,
     DB satırı veya ayar değişikliği öncesinde.
  2. Gemini istemcisi `retry_attempts=1` (SDK retry kapalı; adalet kuralı 3).
  3. Sağlayıcı başına hard-cap (plan K7: claude 50 / gemini 20 / deepseek 5 $)
     `CharacterizationBudget` ile kodda zorlanır; `caps_usd` yalnız AŞAĞI.

Kullanım:
  python -m app.core.benchmark.hybrid_run --workspace 21 --config Aday \
      --capacities ADS=50,SEO=50,SOCIAL=30 --out benchmark/hybrid/21_Aday.json
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.core.benchmark.faz1_ledger import LEDGER_PATH, Faz1Ledger
from app.core.benchmark.claude_bakeoff_plan import (
    assert_workspace_allowed,
    CONTENT_STAGES, O_STAGES, Y_STAGES, faz1_blockers,
)
from app.core.benchmark.reference_characterization import (
    BudgetGuardedAI, CharacterizationBudget, seal, verify_seal,
)
from app.generators.ai_service import (
    AIResponseError, GeminiService, RoutedAIService,
)

PLAN_PATH = "benchmark/claude_bakeoff_plan.json"
CONFIG_NAMES = ("G/G/G", "Aday", "G/G/C")
CONTENT_FAMILIES = ("ads", "seo", "social")
# plan K7 anahtarı -> RoutedAIService provider adı
CAP_PROVIDERS = {"claude": "anthropic", "gemini": "gemini", "deepseek": "deepseek"}
# Maliyet tavanı asıl bağlayıcıdır; istek sayısı yalnız sonsuz-döngü sigortası.
HYBRID_MAX_REQUESTS = 20_000
SOCIAL_BRIEF_KEYWORDS = 5
SOCIAL_BRIEF_TARGETS = (("instagram", "post"), ("instagram", "carousel"))
SOCIAL_IDEAS_PER_CATEGORY = 3
SOCIAL_MAX_IDEAS = 30  # contents isteği üst sınırı


class Faz1Blocked(RuntimeError):
    """Mühürlü plan Faz 1 blokajı taşıyor — ücretli koşu BAŞLAMAZ."""

    def __init__(self, blockers: Sequence[str]):
        self.blockers = list(blockers)
        super().__init__("Faz 1 bloklu: " + ", ".join(self.blockers))


# ── plan / konfigürasyon ────────────────────────────────────────────────

def load_plan(path: str = PLAN_PATH) -> Dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_seal(plan)
    return plan


def build_configs(plan: Mapping[str, Any]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """{config: {stage: {"provider","model"}}}; model kimlikleri PLANDAN."""
    k3 = plan["decisions"]["K3_models"]

    def route(stages, model):
        return {s: {"provider": "anthropic", "model": model} for s in stages}

    content = route(CONTENT_STAGES, k3["content"])
    return {
        "G/G/G": {},
        "Aday": {**route(Y_STAGES, k3["Y"]), **route(O_STAGES, k3["O"]),
                 **content},
        "G/G/C": dict(content),
    }


def resolve_caps(plan: Mapping[str, Any],
                 caps_usd: Optional[Mapping[str, float]] = None
                 ) -> Dict[str, float]:
    """{provider: cap_usd}; `caps_usd` planın tavanını YALNIZ düşürebilir."""
    caps = {CAP_PROVIDERS[k]: float(v)
            for k, v in plan["decisions"]["K7_caps_usd"].items()}
    for key, val in (caps_usd or {}).items():
        prov = CAP_PROVIDERS.get(key, key)
        if prov not in caps:
            raise ValueError("bilinmeyen cap sağlayıcısı: %r" % key)
        if not 0 < float(val) <= caps[prov]:
            raise ValueError(
                "caps_usd[%s]=%s planın tavanını (%s) AŞAMAZ ve >0 olmalı "
                "(yalnız aşağı)" % (key, val, caps[prov]))
        caps[prov] = float(val)
    return caps


# ── rota sabitleyici ────────────────────────────────────────────────────

class StageRoutePinnedAI:
    """Kök `RoutedAIService`'i stage→(provider, model) tablosuyla sabitler.

    İçerik üreticileri `scoped(ai, stage)`'i provider/model'SİZ çağırır;
    motor koşucuları mühürlü değerleri geçer. Tablodaki stage'de sağlayıcı
    bu sarmalayıcıdan enjekte edilir; çağıran FARKLI değer geçerse fail-closed.
    Tabloda olmayan stage Gemini'dir: provider/model açıkça çözülür (böylece
    guard doğru modeli fiyatlar; global AI_STAGE_ROUTES yeniden okunmaz).
    """

    def __init__(self, root: Any, routes: Mapping[str, Mapping[str, str]]):
        self._root = root
        self.routes = {k: dict(v) for k, v in routes.items()}

    def for_stage(self, stage: str, **overrides):
        route = self.routes.get(stage)
        prov, model = overrides.get("provider"), overrides.get("model")
        if route is not None:
            if prov not in (None, route["provider"]) or model not in (
                    None, route["model"]):
                raise AIResponseError(
                    "ROUTE_CONFLICT: stage %r sabitlenmiş rota %s/%s; çağıran "
                    "%s/%s istedi (fail-closed)"
                    % (stage, route["provider"], route["model"], prov, model))
            overrides["provider"], overrides["model"] = (
                route["provider"], route["model"])
        else:
            if prov not in (None, "gemini"):
                raise AIResponseError(
                    "ROUTE_CONFLICT: stage %r Gemini'de kalmalı; çağıran "
                    "provider=%r istedi (fail-closed)" % (stage, prov))
            from app.config import settings

            overrides["provider"] = "gemini"
            overrides["model"] = (model
                                  or (settings.AI_STAGE_MODELS or {}).get(stage)
                                  or settings.GEMINI_MODEL)
        return self._root.for_stage(stage, **overrides)

    @property
    def collector(self):
        return getattr(self._root, "collector", None)

    @collector.setter
    def collector(self, value):
        self._root.collector = value

    @property
    def cost_binding(self):
        return getattr(self._root, "cost_binding", None)

    @cost_binding.setter
    def cost_binding(self, value):
        self._root.cost_binding = value

    def close(self):
        closer = getattr(self._root, "close", None)
        if callable(closer):
            closer()

    def __getattr__(self, name):
        if name == "_root":  # init öncesi özyinelemeyi önle
            raise AttributeError(name)
        return getattr(self._root, name)


def build_benchmark_ai(config_name: str, plan: Mapping[str, Any], *,
                       collector: Any,
                       caps_usd: Optional[Mapping[str, float]] = None,
                       ledger: Optional[Faz1Ledger] = None
                       ) -> BudgetGuardedAI:
    configs = build_configs(plan)
    if config_name not in configs:
        raise ValueError("bilinmeyen konfigürasyon %r (izinli: %s)"
                         % (config_name, ", ".join(configs)))
    caps = resolve_caps(plan, caps_usd)
    if ledger is not None:            # deney TOPLAMI: önceki komutların harcaması düşülür
        caps = ledger.remaining(caps)
    root = RoutedAIService(GeminiService(retry_attempts=1))
    root.collector = collector
    pinned = StageRoutePinnedAI(root, configs[config_name])
    budgets = {
        prov: CharacterizationBudget(
            max_requests=HYBRID_MAX_REQUESTS, max_cost_usd=cap, model=None,
            provider=prov)
        for prov, cap in caps.items()}
    return BudgetGuardedAI(pinned, budgets, collector=collector)


@contextmanager
def routes_override(routes: Mapping[str, Mapping[str, str]]):
    """`settings.AI_STAGE_ROUTES`'u koşu süresince ayarlar (mühür bunu okur)."""
    from app.config import settings

    old = settings.AI_STAGE_ROUTES
    settings.AI_STAGE_ROUTES = {k: dict(v) for k, v in routes.items()}
    try:
        yield
    finally:
        settings.AI_STAGE_ROUTES = old


# ── içerik aileleri (DB'li; birim testte koşmaz) ────────────────────────

def _err(exc: BaseException) -> str:
    return ("%s: %s" % (type(exc).__name__, exc))[:500]


def _run_ads(db, ai, run, defaults) -> Dict[str, Any]:
    from app.generators.ads.ads_generator import AdsGenerator
    from app.generators.ads.generation_sets import (
        dispatch_ads_generation, finalize_ads_failure, finalize_ads_success,
    )
    from app.schemas.ads import AdsGenerateRequest

    name = defaults.get("brand_name") or "Marka"
    usp = defaults.get("brand_usp") or "Kaliteli ürün ve hizmet"
    gen_set = dispatch_ads_generation(
        db, run.id, str(uuid.uuid4()),
        {"operation": "full", "brand_name": name, "display_brand_usp": usp,
         "trusted_brand_usp": "", "max_groups": None, "source": "hybrid_run"})
    try:
        result = AdsGenerator(db, ai).generate_ads(
            AdsGenerateRequest(scoring_run_id=run.id, brand_name=name,
                               brand_usp=usp),
            generation_set_id=gen_set.id, defer_commit=True,
            trusted_brand_usp="")
        if result.total_groups == 0:
            raise RuntimeError("0 reklam grubu üretildi (havuz boş ya da AI "
                               "çıktısı işlenemedi)")
        warns = [w for g in result.ad_groups
                 for w in (g.dropped_negatives or [])]
        status = finalize_ads_success(
            db, gen_set, groups_count=result.total_groups,
            failed_groups=result.failed_groups, warnings=warns,
            extra_result_data={"operation": "full", "source": "hybrid_run"})
        return {"generation_set_id": gen_set.id, "set_status": status,
                "n_groups": result.total_groups,
                "failed_groups": result.failed_groups}
    except Exception as exc:
        finalize_ads_failure(db, gen_set, error_message=_err(exc))
        raise


def _run_seo(db, ai, run, limit) -> Dict[str, Any]:
    from app.core.engine.persistence import load_channel_pools
    from app.generators.seo_geo.seo_geo_generator import SEOGeoGenerator
    from app.schemas.seo_geo import SEOGEOGenerateRequest

    kids = [p.keyword_id for p in load_channel_pools(
        db, scoring_run_id=run.id, channel="SEO")]
    if limit:
        kids = kids[:limit]
    if not kids:
        raise RuntimeError("SEO havuzu boş")
    gen = SEOGeoGenerator(db, ai)
    ids, failed = [], {}
    for kid in kids:  # tek kelime hatası diğerlerini durdurmaz (ITT: kayıtlı)
        try:
            out = gen.generate_content(
                SEOGEOGenerateRequest(keyword_id=kid, tone="informative"),
                scoring_run_id=run.id)
            ids.append(out["id"])
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed[str(kid)] = _err(exc)
    if not ids:  # hiç içerik yok: content_errors'a düşmeli (ADS'deki "0 grup" gibi)
        raise RuntimeError("SEO içeriği üretilemedi: %d/%d kelime başarısız; ilk hata: %s"
                           % (len(failed), len(kids), next(iter(failed.values()))))
    return {"content_ids": ids, "n_requested": len(kids), "failed": failed}


def _run_social(db, ai, run, ws_id, defaults) -> Dict[str, Any]:
    from app.api.v1.generation import generate_social_categories_for_brief
    from app.config import settings
    from app.core.engine.persistence import load_channel_pools
    from app.core.social.brief_service import create_social_brief
    from app.core.social.content_flow import begin_social_content_generation
    from app.core.social.content_orchestration import (
        run_social_content_generation,
    )
    from app.core.social.idea_flow import begin_social_idea_generation
    from app.core.social.idea_orchestration import run_social_idea_generation
    from app.core.social.idea_read import load_social_idea_result
    from app.database.connection import SessionLocal
    from app.schemas.social_brief import (
        SocialBriefCategoriesGenerateRequest, SocialBriefContentsGenerateRequest,
        SocialBriefCreateRequest, SocialBriefIdeasGenerateRequest,
        SocialBriefTargetCreateRequest,
    )
    from starlette.responses import Response

    if not settings.ENABLE_SOCIAL_BRIEF_FLOW:
        raise RuntimeError("ENABLE_SOCIAL_BRIEF_FLOW kapalı")
    kids = [p.keyword_id for p in load_channel_pools(
        db, scoring_run_id=run.id, channel="SOCIAL")][:SOCIAL_BRIEF_KEYWORDS]
    if not kids:
        raise RuntimeError("SOCIAL havuzu boş")
    key = "hybrid-%s-%s" % (run.id, uuid.uuid4().hex[:8])

    brief = create_social_brief(
        db, brand_profile_id=ws_id,
        request=SocialBriefCreateRequest(
            scoring_run_id=run.id, keyword_ids=kids,
            targets=[SocialBriefTargetCreateRequest(platform=p, content_format=f)
                     for p, f in SOCIAL_BRIEF_TARGETS],
            brand_name=defaults.get("brand_name") or None))
    db.commit()
    brief_id = brief.id

    cats = generate_social_categories_for_brief(
        brief_id=brief_id, request=SocialBriefCategoriesGenerateRequest(
            idempotency_key=key + "-cat"),
        response=Response(), brand_profile_id=ws_id, db=db, ai=ai)
    cat_ids = [c.id for c in cats.categories][:6]

    start = begin_social_idea_generation(
        db, brief_id=brief_id, brand_profile_id=ws_id,
        request=SocialBriefIdeasGenerateRequest(
            idempotency_key=key + "-idea", category_ids=cat_ids,
            ideas_per_category=SOCIAL_IDEAS_PER_CATEGORY))
    db.commit()
    ires = run_social_idea_generation(
        session_factory=SessionLocal, ai_service=ai,
        attempt_id=start.attempt_id, task_id=str(uuid.uuid4()))
    db.expire_all()
    ideas = load_social_idea_result(
        db, brief_id=brief_id, attempt_id=start.attempt_id,
        brand_profile_id=ws_id)
    idea_ids = [i.id for i in ideas.ideas][:SOCIAL_MAX_IDEAS]
    if not idea_ids:
        raise RuntimeError("fikir üretilemedi (attempt status=%s)" % ires.status)

    cstart = begin_social_content_generation(
        db, brief_id=brief_id, brand_profile_id=ws_id,
        request=SocialBriefContentsGenerateRequest(
            idempotency_key=key + "-content", idea_ids=idea_ids))
    db.commit()
    cres = run_social_content_generation(
        session_factory=SessionLocal, ai_service=ai,
        attempt_id=cstart.attempt_id, task_id=str(uuid.uuid4()))
    return {"brief_id": brief_id,
            "attempt_ids": {"categories": cats.attempt_id,
                            "ideas": start.attempt_id,
                            "contents": cstart.attempt_id},
            "idea_status": ires.status, "content_status": cres.status,
            "n_ideas": len(idea_ids),
            "persisted_idea_ids": list(cres.persisted_idea_ids),
            "unresolved_idea_ids": list(cres.unresolved_idea_ids)}


# ── ana akış ────────────────────────────────────────────────────────────

def _work_plan(config_name, routes, content, caps, capacities, ws_id,
               seo_keyword_limit) -> Dict[str, Any]:
    return {
        "kind": "hybrid_dry_run", "workspace_id": ws_id,
        "config_name": config_name, "routes": routes, "caps_usd": caps,
        "capacities": dict(capacities) if capacities else None,
        "steps": ["create v3 ScoringRun + freeze_universe_snapshot",
                  "run_v3_orchestration (rota muhürlenir)"]
                 + ["content:" + c for c in content],
        "pool_sizes": "n/a", "seo_keyword_limit": seo_keyword_limit,
        "social": {"keywords": SOCIAL_BRIEF_KEYWORDS,
                   "targets": [list(t) for t in SOCIAL_BRIEF_TARGETS]},
        "ai_calls": 0,
    }


def run_hybrid(db, *, workspace_id: int, config_name: str,
               plan: Mapping[str, Any], collector: Any,
               capacities: Optional[Mapping[str, int]] = None,
               content: Sequence[str] = CONTENT_FAMILIES,
               seo_keyword_limit: Optional[int] = None,
               dry_run: bool = False, ai: Any = None,
               caps_usd: Optional[Mapping[str, float]] = None,
               ledger: Optional[Faz1Ledger] = None
               ) -> Dict[str, Any]:
    # 1) Codex #1: bloklayıcı varsa HİÇBİR şey kurulmadan dur.
    blockers = faz1_blockers(dict(plan))
    if blockers:
        raise Faz1Blocked(blockers)
    # 1b) Codex Faz 1 #3: hedef ws planın 1B kapsamında ONAYLI olmalı.
    assert_workspace_allowed(dict(plan), workspace_id, "1B")
    configs = build_configs(plan)
    if config_name not in configs:
        raise ValueError("bilinmeyen konfigürasyon %r" % config_name)
    content = tuple(content)
    bad = [c for c in content if c not in CONTENT_FAMILIES]
    if bad:
        raise ValueError("bilinmeyen içerik ailesi: %s" % bad)
    routes = configs[config_name]
    caps = resolve_caps(plan, caps_usd)
    if dry_run:
        return _work_plan(config_name, routes, content, caps, capacities,
                          workspace_id, seo_keyword_limit)
    if not capacities or not all(capacities.get(c) for c in
                                 ("ADS", "SEO", "SOCIAL")):
        raise ValueError("capacities (ADS/SEO/SOCIAL) zorunlu — adalet: tüm "
                         "konfigürasyonlarda aynı olmalı")
    if "social" in content:
        from app.config import settings
        if not settings.ENABLE_SOCIAL_BRIEF_FLOW:
            raise RuntimeError("ENABLE_SOCIAL_BRIEF_FLOW kapalı — ücretli "
                               "çağrıdan ÖNCE durduruldu")

    from app.core.engine.context import (
        freeze_universe_snapshot, universe_fingerprint,
    )
    from app.core.engine.orchestrator import run_v3_orchestration
    from app.core.engine.persistence import manifest_section
    from app.core.scoring.score_engine import ScoreEngine
    from app.core.scoring.state_machine import (
        begin_channel_assignment, transition, transition_atomic,
    )
    from app.core.site_analyzer.brand_defaults import BrandDefaultsResolver

    if ledger is None:                # Codex Faz 1 #1: komutlar arası toplam
        ledger = Faz1Ledger.open(LEDGER_PATH)
    ai = ai or build_benchmark_ai(config_name, plan, collector=collector,
                                  caps_usd=caps_usd, ledger=ledger)
    clock: Dict[str, float] = {}
    errors: Dict[str, str] = {}
    out_content: Dict[str, Any] = {}
    try:
        with routes_override(routes):
            t0 = time.monotonic()
            run = ScoreEngine(db).create_scoring_run(
                ads_capacity=capacities["ADS"], seo_capacity=capacities["SEO"],
                social_capacity=capacities["SOCIAL"],
                run_name="hybrid_%s_ws%s_%s" % (
                    config_name.replace("/", ""), workspace_id,
                    datetime.now().strftime("%Y%m%d_%H%M%S")),
                brand_profile_id=workspace_id, enable_ads=True,
                enable_seo=True, enable_social=True,
                auto_assign_channels=True, algorithm_version="v3")
            collector.scoring_run_id = run.id
            if not transition_atomic(db, run, "scoring", "pending"):
                raise RuntimeError("run pending->scoring CAS başarısız")
            fingerprint = universe_fingerprint(freeze_universe_snapshot(db, run))
            db.commit()
            if not transition_atomic(db, run, "scored", "scoring"):
                raise RuntimeError("run scoring->scored CAS başarısız")
            if not begin_channel_assignment(db, run, "scored"):
                raise RuntimeError("run scored->channel_assigning başarısız")
            clock["setup"] = round(time.monotonic() - t0, 3)

            t0 = time.monotonic()
            try:
                delivery = run_v3_orchestration(
                    db, run=run, ai=ai, capacities=dict(capacities))
            except Exception:  # orkestrasyon hatası ölümcül (kayıt: failed)
                db.rollback()
                try:
                    transition(db, run, target="failed")
                except Exception:  # noqa: BLE001
                    pass
                raise
            clock["orchestration"] = round(time.monotonic() - t0, 3)
            db.refresh(run)
            section = manifest_section(run)
            defaults = BrandDefaultsResolver(db).resolve(run.id) or {}

            for fam in content:
                t0 = time.monotonic()
                try:
                    if fam == "ads":
                        out_content[fam] = _run_ads(db, ai, run, defaults)
                    elif fam == "seo":
                        out_content[fam] = _run_seo(
                            db, ai, run, seo_keyword_limit)
                    else:
                        out_content[fam] = _run_social(
                            db, ai, run, workspace_id, defaults)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    errors[fam] = _err(exc)
                clock["content_" + fam] = round(time.monotonic() - t0, 3)
    finally:
        flush = getattr(collector, "flush", None)
        if callable(flush):
            flush()
        closer = getattr(ai, "close", None)
        if callable(closer):
            closer()
        summ = getattr(ai, "summary", None)
        if callable(summ):                # harcama deftere işlenir (taahhüt, muhafazakâr)
            ledger.record(kind="hybrid_run",
                          ref="ws%s:%s" % (workspace_id, config_name),
                          budget_summary=summ())
            ledger.save()

    return seal({
        "kind": "hybrid_run", "workspace_id": workspace_id,
        "config_name": config_name, "routes": routes,
        "scoring_run_id": run.id, "universe_fingerprint": fingerprint,
        "capacities": dict(capacities), "delivery_summary": _jsonable(delivery),
        "manifest_section": _jsonable(section),
        "content": out_content, "content_errors": errors,
        "budget_summary": _jsonable(ai.summary()),
        "usage": _jsonable(collector.summary()),
        "wall_clock_s": clock,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _parse_capacities(text: str) -> Dict[str, int]:
    out = {}
    for part in text.split(","):
        k, v = part.split("=")
        out[k.strip().upper()] = int(v)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", type=int, required=True)
    ap.add_argument("--config", required=True, choices=CONFIG_NAMES)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", default=PLAN_PATH)
    ap.add_argument("--capacities", help="ADS=50,SEO=50,SOCIAL=30")
    ap.add_argument("--content", default=",".join(CONTENT_FAMILIES))
    ap.add_argument("--seo-keyword-limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from app.core.telemetry import UsageCollector
    from app.database.connection import SessionLocal

    plan = load_plan(a.plan)
    db = SessionLocal()
    try:
        res = run_hybrid(
            db, workspace_id=a.workspace, config_name=a.config, plan=plan,
            collector=UsageCollector(brand_profile_id=a.workspace),
            capacities=_parse_capacities(a.capacities) if a.capacities else None,
            content=tuple(c for c in a.content.split(",") if c),
            seo_keyword_limit=a.seo_keyword_limit, dry_run=a.dry_run)
    except Faz1Blocked as exc:
        print("BLOKE: %s" % exc)
        return 2
    finally:
        db.close()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:  # LF
        fh.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
