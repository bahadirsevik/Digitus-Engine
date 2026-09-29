# -*- coding: utf-8 -*-
"""Assignment preflight: onaya giden sayıların TEK üreticisi (plan §6.3).

Sözleşme:
- Preflight FAIL-CLOSED'dır. Bağlam, evren, fiyat ya da telemetri eksikse
  tahmin UYDURULMAZ; tipli hata döner ve dispatch başlamaz.
- Döndürülen üç tavan (screening / downstream / combined) YALNIZ onay
  içindir; delinemeyen sınır kalıcı `AiCostReservation` ledger'ıdır.
  Attempt'e yazılan cap'ler buradan gelir ve ledger DB'yi otorite sayar.
- `preflight_sha256` onayı BAĞLAR: kullanıcı neyi onayladıysa dispatch o
  girdilerle çalışır; evren/bağlam/sürüm/kapasite değişirse SHA değişir ve
  eski onay geçersizdir.

Screening tarafında İKİ sayı üretilir (Codex 10. tur #7):
- `screening_expected_usd`: geçmiş telemetriden BEKLENTİ.
- `screening_single_pass_ceiling_usd`: retry'siz REZERVASYON TAVANI (her
  isteği MAX_OUTPUT_TOKENS dolmuş kabul eder — beklenen maliyet DEĞİL).
- `screening_hard_cap_usd`: runner'ın GERÇEK retry topolojisinden türeyen
  üst sınır — batch başına (1 + parse + transient) × (1 + missing) deneme
  ve koşu başına `SINGLE_RETRY_LIMIT` tekil çağrı fiyatlanır. Bu, ledger'ın
  istek başına ayırdığı tavanla AYNI formülün toplamıdır; istatistiksel
  bir "retry payı katsayısı" DEĞİLDİR. LEDGER YALNIZ BUNU UYGULAR.
Downstream tarafı GEÇMİŞ TELEMETRİDEN türetilmiş bir TAHMİNDİR.
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_CEILING, Decimal
from typing import Any, Dict, List, Optional, Sequence

from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCREENING_APPLIED_CHANNELS_V3,
    UNION_CONTRACT_V3,
    downstream_request_plan,
)
from app.core.screening.context import (
    ScreeningContextMissing,
    canonical_screening_context,
)
from app.core.screening.ensemble import sticky_plans
from app.core.screening.identity import (
    channel_rank_snapshot_sha256,
    relevance_rows_sha256,
    screening_input_identity,
    screening_runner_contract,
)

PREFLIGHT_CONTRACT_VERSION = "SCRPRE-2026-07-30-v1"
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ASSISTIVE = "assistive"
SCREENING_MODES = (MODE_OFF, MODE_SHADOW, MODE_ASSISTIVE)
# Mod → (uygulanan çarpan, counterfactual hedef çarpanı); DB CHECK ile aynı
MODE_MULTIPLIERS = {MODE_OFF: (1, 1), MODE_SHADOW: (1, 3),
                    MODE_ASSISTIVE: (3, 3)}
CHANNELS = ("ADS", "SEO", "SOCIAL")


class PreflightError(RuntimeError):
    """Tipli preflight reddi — API 409/422'ye çevirir, tahmin üretilmez."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _money(value: float) -> Decimal:
    """Para YUKARI yuvarlanır: tavan aşağı yuvarlanarak delinmez."""
    return Decimal(str(value)).quantize(Decimal("0.000001"),
                                        rounding=ROUND_CEILING)


def resolve_screening_mode(run, settings) -> str:
    """Etkin mod: bayrak KAPALIYSA her zaman `off` (kill switch)."""
    if not getattr(settings, "ENABLE_CORPUS_SCREENING", False):
        return MODE_OFF
    pref = (getattr(run, "screening_preference", None) or MODE_OFF).strip()
    return pref if pref in SCREENING_MODES else MODE_OFF


def load_universe_rows(db, scoring_run_id: int) -> List[Dict[str, Any]]:
    """Run'ın skorlanmış evreni — dondurulacak satırlar (fail-closed).

    Codex 10. tur #5: satırlar YALNIZ kimlik/metin değil, kanal SKOR ve
    RANK'leri ile relevance'ı da taşır. Shadow counterfactual'ı canlı
    tabloları okursa parent aynı anda relevance'ı tazelediğinde ölçüm
    zamanlamaya göre değişirdi; snapshot bunu imkânsız kılar.
    """
    from app.database.models import Keyword, KeywordRelevance, KeywordScore

    relevance = {r.keyword_id: float(r.relevance_score)
                 for r in db.query(KeywordRelevance)
                 .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
                 .all()}
    rows = (db.query(KeywordScore, Keyword.keyword)
            .join(Keyword, Keyword.id == KeywordScore.keyword_id)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(KeywordScore.keyword_id.asc())
            .all())
    out = []
    for score, text in rows:
        if not text or not str(text).strip():
            raise PreflightError(
                "UNIVERSE_INVALID",
                f"keyword {score.keyword_id} metni boş — evren dondurulamaz")
        out.append({
            "keyword_score_id": int(score.id),
            "keyword_id": int(score.keyword_id),
            "keyword": str(text),
            "scores": {ch: float(getattr(score, f"{ch.lower()}_score") or 0.0)
                       for ch in CHANNELS},
            "ranks": {ch: getattr(score, f"{ch.lower()}_rank")
                      for ch in CHANNELS},
            "relevance": relevance.get(score.keyword_id),
        })
    return out


def _single_request_ceiling_usd(context_fields: Dict[str, str],
                                universe: Sequence[Dict[str, Any]],
                                model: str, prompt_version: str) -> float:
    """EN PAHALI tek-keyword isteğinin tavanı (Codex 12. tur #4).

    Tekil retry maliyeti ortalama batch isteğinden türetilemez: tek
    kelimelik prompt farklı boyuttadır. Üst sınır iddiası ancak gerçek
    tek-keyword tavanıyla doğrudur.
    """
    from app.core.screening.contract import build_prompt
    from app.core.screening.runner import ScreeningBudget

    budget = ScreeningBudget(model=model, max_requests=None,
                             max_cost_usd=None)
    # Codex 13. tur #4: "en uzun kelime" TAHMİN EDİLMEZ — her satırın
    # GERÇEK UTF-8 prompt boyutu hesaplanıp maksimumu alınır (Türkçe
    # karakterler ve id uzunluğu bayt sayısını değiştirir; 1500 satır için
    # maliyet önemsizdir).
    ceiling = 0.0
    for item in universe:
        prompt = build_prompt(
            product_definition=context_fields["product_definition"],
            content_strategy=context_fields["content_strategy"],
            target_audience=context_fields.get("target_audience") or "-",
            social_mode=context_fields["social_mode"],
            keywords=[{"id": item["id"], "keyword": item["keyword"]}],
            prompt_version=prompt_version)
        ceiling = max(ceiling,
                      budget.per_request_ceiling(len(prompt.encode("utf-8"))))
    if ceiling <= 0:
        raise PreflightError(
            "SCREENING_PRICING_UNAVAILABLE",
            f"'{model}' fiyat tablosunda yok — tekil retry tavanı "
            f"hesaplanamaz (fail-closed)")
    return ceiling


def _screening_ceiling_usd(context_fields: Dict[str, str],
                           batches: Sequence[Sequence[Dict[str, Any]]],
                           model: str, prompt_version: str) -> float:
    """Planlanan isteklerin TOPLAM tavanı (ledger ile aynı formül)."""
    from app.core.screening.contract import build_prompt
    from app.core.screening.runner import ScreeningBudget

    budget = ScreeningBudget(model=model, max_requests=None,
                             max_cost_usd=None)
    total = 0.0
    for batch in batches:
        prompt = build_prompt(
            product_definition=context_fields["product_definition"],
            content_strategy=context_fields["content_strategy"],
            target_audience=context_fields.get("target_audience") or "-",
            social_mode=context_fields["social_mode"],
            keywords=[{"id": k["id"], "keyword": k["keyword"]}
                      for k in batch],
            prompt_version=prompt_version)
        ceiling = budget.per_request_ceiling(len(prompt.encode("utf-8")))
        if ceiling <= 0:
            raise PreflightError(
                "SCREENING_PRICING_UNAVAILABLE",
                f"'{model}' fiyat tablosunda yok — screening tavanı "
                f"hesaplanamaz (fail-closed)")
        total += ceiling
    return total


def _screening_expected_usd(db, planned_requests: int,
                            model: str) -> Optional[Dict[str, Any]]:
    """Tarama için KABA telemetrik beklenti (Codex 14/15. tur).

    `screening_single_pass_ceiling_usd` her isteği MAX_OUTPUT_TOKENS
    doldurmuş kabul eden REZERVASYON TAVANIDIR; beklenen maliyet değildir.
    Gerçek beklenti geçmiş `corpus_screening` telemetrisinden gelir.
    Telemetri yoksa None döner (bu alan bilgilendirmedir; uygulanan sınır
    değildir — dolayısıyla fail-closed davranmaz).
    """
    from app.core.benchmark.reference_characterization import (
        CharacterizationError,
        stage_unit_costs,
    )

    try:
        unit = stage_unit_costs(db, model, ("corpus_screening",))
    except CharacterizationError:
        return None
    block = unit["corpus_screening"]
    return {
        "expected_usd": round(planned_requests * block["mean_usd"], 6),
        "p90_usd": round(planned_requests * block["p90_usd"], 6),
        "samples": block["samples"],
        "basis": ("geçmiş KARMA corpus_screening telemetrisi × planlanan "
                  "istek — KABA TAHMİN. Örneklem prompt sürümü, batch "
                  "boyutu ve ana/retry çağrısı ayrımı YAPMADAN süzülür "
                  "(bake-off ve deney koşuları da dahildir); rezervasyon "
                  "tavanı DEĞİLDİR ve 'üretim beklenen maliyeti' diye "
                  "sunulamaz. Gerçek shadow sonrası job/checkpoint "
                  "verisiyle kalibre edilecek."),
    }


def _downstream_estimate(db, run, *, targets: Dict[str, int],
                         brand_filter_active: bool) -> Dict[str, Any]:
    """Gemini downstream TAHMİNİ — geçmiş telemetriden, fail-closed."""
    from app.config import settings
    from app.core.benchmark.reference_characterization import (
        RETRY_CEILING_FACTOR,
        CharacterizationError,
        stage_unit_costs,
    )
    from app.core.constants import (
        BRAND_FILTER_BATCH_SIZE,
        INTENT_BATCH_SIZE,
        PREFILTER_BATCH_SIZE,
        SEO_METADATA_BATCH_SIZE,
    )

    plan = downstream_request_plan(
        per_channel_targets=targets,
        seo_capacity=int(run.seo_capacity or 0),
        intent_batch=INTENT_BATCH_SIZE,
        prefilter_batch=PREFILTER_BATCH_SIZE,
        brand_batch=BRAND_FILTER_BATCH_SIZE,
        metadata_batch=SEO_METADATA_BATCH_SIZE,
        brand_filter_active=brand_filter_active)
    requests = plan["requests"]
    stage_req = {
        "intent": requests["intent_total"],
        "ads_prefilter": requests["ads_prefilter"],
        "social_prefilter": requests["social_prefilter"],
        "seo_metadata": requests["seo_metadata"],
    }
    if brand_filter_active:
        stage_req["brand_filter"] = requests["brand_filter"]
    try:
        unit = stage_unit_costs(db, settings.GEMINI_MODEL,
                                tuple(sorted(stage_req)))
    except CharacterizationError as exc:
        raise PreflightError(
            "DOWNSTREAM_PRICING_UNAVAILABLE",
            f"downstream birim maliyeti telemetriden çıkarılamadı: {exc}"
        ) from exc
    expected = sum(stage_req[s] * unit[s]["mean_usd"] for s in stage_req)
    p90 = sum(stage_req[s] * unit[s]["p90_usd"] for s in stage_req)
    worst_p90 = max(unit[s]["p90_usd"] for s in stage_req)
    worst_mean = max(unit[s]["mean_usd"] for s in stage_req)
    transfer = requests["transfer_intent_upper"]
    expansion = requests["expansion_ai_upper"]
    expected_upper = (expected + transfer * unit["intent"]["mean_usd"]
                      + expansion * worst_mean)
    p90_upper = (p90 + transfer * unit["intent"]["p90_usd"]
                 + expansion * worst_p90)
    return {
        "requests": requests,
        "expected_usd": round(expected, 6),
        "expected_upper_usd": round(expected_upper, 6),
        "p90_upper_usd": round(p90_upper, 6),
        "hard_cap_usd": float(_money(p90_upper * RETRY_CEILING_FACTOR)),
        "retry_ceiling_factor": RETRY_CEILING_FACTOR,
        "unit_cost_model": settings.GEMINI_MODEL,
        "basis": "geçmiş AiUsageEvent telemetrisi — TAHMİN, sınır ledger'dır",
    }


def relevance_recompute_pending(run, workspace, settings=None) -> bool:
    """Parent atama başlarken relevance'ı YENİLEYECEK mi?

    Codex 11. tur #4: preflight mevcut relevance satırlarını dondurur;
    parent sonradan `recompute_relevance=True` ile bunları yenilerse
    counterfactual, gerçek atamanın kullandığı evrenden BAŞKA bir
    relevance ile ölçüm yapar. Bu yüzden tarama o durumda başlatılmaz.
    """
    from app.config import settings as default_settings

    settings = settings or default_settings
    if workspace is None:
        return False
    required = bool(getattr(settings, "ENABLE_RELEVANCE_RERANK", False)
                    and not getattr(run, "skip_relevance", False))
    if not required:
        return False
    anchor = int(getattr(workspace, "anchor_version", 1) or 1)
    current = getattr(run, "relevance_anchor_version", None)
    return current is None or current != anchor


def build_assignment_preflight(db, run, workspace, *, mode: str,
                               relevance_coefficient: Optional[float] = None,
                               settings=None) -> Dict[str, Any]:
    """Shadow/assistive dispatch'in onay paketi (fail-closed).

    `off` için preflight ÜRETİLMEZ: off yolu bugünkü davranıştır, cap/job
    almaz. Çağıran `resolve_screening_mode` ile modu belirler.
    """
    from app.config import settings as default_settings
    from app.core.benchmark.reference_characterization import (
        RETRY_CEILING_FACTOR,
    )

    settings = settings or default_settings
    if mode not in (MODE_SHADOW, MODE_ASSISTIVE):
        raise PreflightError("SCREENING_MODE_INVALID",
                             f"preflight yalnız shadow/assistive için "
                             f"üretilir: {mode!r}")
    if not getattr(settings, "ENABLE_CORPUS_SCREENING", False):
        raise PreflightError(
            "SCREENING_DISABLED",
            "ENABLE_CORPUS_SCREENING kapalı — tarama dispatch edilemez")
    # Codex 23. tur #1: atama asamalarindan biri Gemini DISINA route
    # edilmisse downstream cagrilar ledger'a baglanamaz -> tarama
    # baslatilmaz (shadow'da sessiz baseline, assistive'de tipli red)
    from app.core.telemetry.downstream_ledger import (
        assignment_routes_are_ledgered,
    )

    bad_routes = assignment_routes_are_ledgered(settings)
    if bad_routes:
        raise PreflightError(
            "DOWNSTREAM_ROUTE_NOT_LEDGERED",
            f"atama asamalari Gemini disina route edilmis ({bad_routes}); "
            f"bu cagrilar butceye baglanamaz — tarama baslatilmaz")
    if workspace is None:
        raise PreflightError("WORKSPACE_REQUIRED",
                             "workspace olmadan tarama bağlamı kurulamaz")
    try:
        context = canonical_screening_context(workspace)
    except ScreeningContextMissing as exc:
        raise PreflightError("SCREENING_CONTEXT_MISSING", str(exc)) from exc
    if relevance_recompute_pending(run, workspace, settings):
        raise PreflightError(
            "RELEVANCE_RECOMPUTE_PENDING",
            "atama başlarken relevance YENİDEN hesaplanacak; dondurulmuş "
            "tarama evreni gerçek atamayla aynı olmaz — önce relevance'ı "
            "güncelleyin (fail-closed)")

    rows = load_universe_rows(db, run.id)
    if not rows:
        raise PreflightError("UNIVERSE_EMPTY",
                             "run'da skorlanmış kelime yok")
    limit = int(getattr(settings, "CORPUS_SCREENING_MAX_KEYWORDS", 0) or 0)
    if limit and len(rows) > limit:
        raise PreflightError(
            "UNIVERSE_TOO_LARGE",
            f"evren {len(rows)} kelime > sınır {limit} — tarama "
            f"başlatılmaz (maliyet koruması)")

    # Kapsam GERCEK aktif kanal kumesinden turer (Codex 24. tur #3):
    # ADS veya SEO kapali bir run'da o kanal icin ne kimlik ne audit
    # uretilir; kapali kanala `is_applied=true` satiri yazilamaz.
    applied_channels = tuple(
        ch for ch in SCREENING_APPLIED_CHANNELS_V3
        if bool(getattr(run, f"enable_{ch.lower()}", True)))
    if not applied_channels:
        raise PreflightError(
            "NO_APPLIED_CHANNELS",
            "kapsam ici kanallarin (ADS/SEO) hepsi kapali — tarama "
            "uygulanabilir bir kanal bulamaz")

    contract = PRODUCTION_SCREENING_CONTRACT
    universe = [{"id": r["keyword_id"], "keyword": r["keyword"]}
                for r in rows]
    salts = list(contract["view_salts"])
    plans = sticky_plans(universe, int(contract["batch_size"]), salts=salts)
    batches = [batch for plan in plans for batch in plan]
    planned_requests = len(batches)

    identity = screening_input_identity(
        scoring_run_id=int(run.id),
        universe_rows=[{"keyword_id": r["keyword_id"],
                        "text": r["keyword"]} for r in rows],
        context_sha256=context["context_sha256"],
        applied_screening_channels=applied_channels,
        contract=contract, union_contract_version=UNION_CONTRACT_V3,
        runner_contract=screening_runner_contract())

    ceiling_sum = _screening_ceiling_usd(
        context["fields"], batches, contract["model"],
        contract["prompt_version"])
    # Runner'ın GERÇEK retry topolojisi (Codex 10. tur #7):
    #   batch başına (1 + parse + transient) × (1 + missing) deneme
    #   + görünüm başına SINGLE_RETRY_LIMIT tekil çağrı
    from app.core.screening.runner import (
        MAX_MISSING_RETRIES,
        MAX_PARSE_RETRIES,
        MAX_TRANSIENT_RETRIES,
        SINGLE_RETRY_LIMIT,
    )

    call_attempts = 1 + MAX_PARSE_RETRIES + MAX_TRANSIENT_RETRIES
    attempts_per_batch = call_attempts * (1 + MAX_MISSING_RETRIES)
    # Codex 12. tur #4: tekil retry GERÇEK tek-keyword tavanıyla fiyatlanır
    single_ceiling = _single_request_ceiling_usd(
        context["fields"], universe, contract["model"],
        contract["prompt_version"])
    # Codex 11. tur #1: tekil retry kotası GÖRÜNÜM başınadır (runner v2)
    # ve her tekil çağrı da parse/transient retry yapabilir
    single_retry_calls = SINGLE_RETRY_LIMIT * len(salts) * call_attempts
    worst_case = (ceiling_sum * attempts_per_batch
                  + single_retry_calls * single_ceiling)
    screening_cap = float(_money(worst_case))
    admin_limit = float(getattr(
        settings, "CORPUS_SCREENING_MAX_APPROVED_USD", 0) or 0)
    # `Optional` import'u zaten var; expected yukarıda hesaplanır
    if admin_limit and screening_cap > admin_limit:
        # Codex 12. tur #4: mesaj EYLEME DÖNÜK olmalı — hangi sayıya
        # ihtiyaç olduğu ve tek geçişin ne kadar olduğu görünsün.
        # Varsayılan sınır BİLİNÇLİ olarak düşüktür: gerçek para harcayan
        # tarama, açık bir yönetici kararı olmadan başlayamaz.
        raise PreflightError(
            "SCREENING_CAP_EXCEEDS_LIMIT",
            f"gerekli screening tavanı ${screening_cap} > yönetici sınırı "
            f"${admin_limit} (evren {len(rows)} kelime, {planned_requests} "
            f"istek, retry'siz tek geçiş ${float(_money(ceiling_sum))}) — "
            f"koşu başlatılmaz; CORPUS_SCREENING_MAX_APPROVED_USD en az "
            f"${screening_cap} olmalı")

    applied_mult, counterfactual_mult = MODE_MULTIPLIERS[mode]
    capacities = {"ADS": int(run.ads_capacity or 0),
                  "SEO": int(run.seo_capacity or 0),
                  "SOCIAL": int(run.social_capacity or 0)}
    # Downstream hedefi UYGULANAN çarpanla büyür (shadow'da 1 = bugünkü
    # maliyet; counterfactual materyalizasyon Gemini çağrısı YAPMAZ)
    targets = {ch: capacities[ch] * applied_mult for ch in CHANNELS}
    exclude_themes = ((getattr(workspace, "profile_data", None) or {})
                      .get("exclude_themes") or [])
    downstream = _downstream_estimate(
        db, run, targets=targets, brand_filter_active=bool(exclude_themes))
    # Her ücretli screening attempt'i downstream Gemini çağrılarını aynı
    # kalıcı ledger'a bağlar. Shadow canlı havuzu değiştirmez ama parent
    # assignment yine gerçek Gemini çağrıları yaptığı için onun cap'i de
    # uygulanır. Bu ayrıca Faz 2A'nın 1x baseline kontrol kolunu bütçe altında
    # çalıştırmasını sağlar.
    downstream_enforced = True
    enforced_scopes = ["screening", "downstream"]
    combined_exposure = float(_money(screening_cap
                                     + downstream["hard_cap_usd"]))
    downstream_limit = float(getattr(
        settings, "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 0) or 0)
    # Codex 15. tur #2: bu guard YALNIZ downstream'i gerçekten etkileyen
    # modda anlamlıdır. Shadow'da downstream zaten normal atamanın
    # maliyetidir (çarpan 1) ve ledger'a bağlı değildir; tahmin sınırı
    # aşınca DeepSeek taramasını bloklamak GEREKSİZ engel olurdu.
    if (downstream_limit
            and downstream["hard_cap_usd"] > downstream_limit):
        raise PreflightError(
            "DOWNSTREAM_ESTIMATE_EXCEEDS_LIMIT",
            f"downstream tahmini ${downstream['hard_cap_usd']} > yönetici "
            f"sınırı ${downstream_limit} — koşu başlatılmaz "
            f"(CORPUS_DOWNSTREAM_MAX_APPROVED_USD)")
    expected = _screening_expected_usd(db, planned_requests,
                                       contract["model"])

    rank_sha = channel_rank_snapshot_sha256(
        {ch: {r["keyword_id"]: (r["ranks"][ch] or 10 ** 9) for r in rows}
         for ch in CHANNELS})
    relevance_present = all(r["relevance"] is not None for r in rows)
    rel_sha = (relevance_rows_sha256(
        {r["keyword_id"]: r["relevance"] for r in rows})
        if relevance_present else None)
    payload = {
        "preflight_contract_version": PREFLIGHT_CONTRACT_VERSION,
        "union_contract_version": UNION_CONTRACT_V3,
        "screening_mode": mode,
        "scoring_run_id": int(run.id),
        "brand_profile_id": int(run.brand_profile_id),
        "universe_size": len(rows),
        "universe_sha256": identity["components"]["universe_sha256"],
        "context_sha256": context["context_sha256"],
        "screening_input_identity_sha256": identity["sha256"],
        "applied_screening_channels": list(applied_channels),
        "applied_candidate_multiplier": applied_mult,
        "counterfactual_target_multiplier": counterfactual_mult,
        "capacities": capacities,
        "downstream_targets": targets,
        "planned_screening_requests": planned_requests,
        "channel_rank_snapshot_sha256": rank_sha,
        "relevance_rows_sha256": rel_sha,
        # Retry'siz REZERVASYON TAVANI (beklenen maliyet DEĞİL)
        "screening_single_pass_ceiling_usd": float(_money(ceiling_sum)),
        "screening_worst_case_requests": int(
            planned_requests * attempts_per_batch + single_retry_calls),
        # Teknik UI geriye uyumu: bu alan yalnız screening cap'idir.
        "screening_hard_cap_usd": screening_cap,
        "enforced_hard_cap_usd": screening_cap,
        "enforced_scopes": enforced_scopes,
        # Shadow ve assistive'de aynı attempt ledger'ında uygulanır.
        "downstream_hard_cap_usd": downstream["hard_cap_usd"],
        "downstream_enforced": downstream_enforced,
        "combined_exposure_usd": combined_exposure,
        "algorithm_version": getattr(run, "algorithm_version", "v2") or "v2",
        "policy_version": int(getattr(workspace, "policy_version", 1) or 1),
        "anchor_version": int(getattr(workspace, "anchor_version", 1) or 1),
        "strategy_fingerprint": context.get("strategy_fingerprint"),
        # Codex 11. tur #8: job denetimi sürüm alanını da taşımalı
        "strategy_version": int(context.get("strategy_version") or 0),
        "relevance_coefficient": (None if relevance_coefficient is None
                                  else float(relevance_coefficient)),
    }
    # SHA ve doğrulayıcı AYNI anahtar listesini kullanır (tek kaynak);
    # telemetrik beklenti onay hash'ine GİRMEZ — zamanla değişir ve
    # kullanıcının onayını sebepsiz geçersiz kılardı
    preflight_sha = preflight_sha_for(payload)
    return {
        **payload,
        # KABA tahmin (karma telemetri) — onay SHA'sına girmez
        "screening_expected_rough_usd": (expected["expected_usd"]
                                         if expected else None),
        "screening_expected_rough_p90_usd": (expected["p90_usd"]
                                             if expected else None),
        "screening_expected_samples": (expected["samples"]
                                       if expected else 0),
        "preflight_sha256": preflight_sha,
        # Onaya GİRMEYEN yardımcı alanlar (SHA payload'ında yok):
        "universe_rows": rows,
        "context": context,
        "identity_components": identity["components"],
        "screening_ceiling_sum_usd": round(ceiling_sum, 6),
        "retry_ceiling_factor": RETRY_CEILING_FACTOR,
        "downstream_estimate": downstream,
        "screening_retry_topology": {
            "attempts_per_batch": attempts_per_batch,
            "single_retry_calls": single_retry_calls,
            "single_retry_scope": "view",
            "basis": ("runner sabitleri: parse %d + transient %d + missing "
                      "%d turu; görünüm başına %d tekil çağrı (PAYLAŞILAN "
                      "bütçe) ve her tekil çağrı %d denemeye kadar"
                      % (MAX_PARSE_RETRIES, MAX_TRANSIENT_RETRIES,
                         MAX_MISSING_RETRIES, SINGLE_RETRY_LIMIT,
                         call_attempts))},
        "screening_expected_basis": (expected["basis"] if expected
                                     else "telemetri yok — beklenti "
                                          "hesaplanmadı"),
        "cost_semantics": (
            "ÜÇ FARKLI SAYI: (1) screening_expected_rough_usd = geçmiş "
            "KARMA telemetriden kaba beklenti; "
            "(2) screening_single_pass_ceiling_usd = her isteği "
            "MAX_OUTPUT_TOKENS dolmuş kabul eden retry'siz rezervasyon "
            "tavanı; (3) screening_hard_cap_usd = runner'ın gerçek retry "
            "topolojisiyle üst sınır — LEDGER BUNU UYGULAR. "
            "Shadow ve assistive modlarında downstream_hard_cap_usd aynı "
            "attempt ledger'ında UYGULANIR; iki cap'in toplamı "
            "combined_exposure_usd'dir ve downstream alanları kimlik "
            "SHA'sına dahildir."),
    }


# Onay hash'inin TABANI: girdiler + UYGULANAN sınır. Bilgi amaçlı
# tahminler (downstream, toplam maruziyet) BURADA YOKTUR — yeni Gemini
# telemetrisi geldiğinde kullanıcının screening onayı sebepsiz
# geçersizleşmemelidir (Codex 16. tur).
_SHA_BASE_KEYS = ("preflight_contract_version", "union_contract_version",
                  "screening_mode", "scoring_run_id", "brand_profile_id",
                  "universe_size", "universe_sha256", "context_sha256",
                  "screening_input_identity_sha256",
                  "applied_screening_channels",
                  "applied_candidate_multiplier",
                  "counterfactual_target_multiplier", "capacities",
                  "downstream_targets", "planned_screening_requests",
                  "channel_rank_snapshot_sha256", "relevance_rows_sha256",
                  "screening_single_pass_ceiling_usd",
                  "screening_worst_case_requests",
                  "screening_hard_cap_usd", "enforced_hard_cap_usd",
                  "enforced_scopes",
                  "algorithm_version", "policy_version",
                  "anchor_version", "strategy_fingerprint",
                  "strategy_version", "relevance_coefficient")
# Downstream ledger'a bağlanan ücretli screening modlarında kimliğin parçasıdır.
_SHA_DOWNSTREAM_KEYS = ("downstream_hard_cap_usd", "downstream_enforced",
                        "combined_exposure_usd")


def preflight_sha_for(payload: Dict[str, Any]) -> str:
    """Kayıtlı bir preflight sözleşmesinin SHA'sını yeniden üretir.

    Downstream alanları hash'e yalnızca uygulanıyorsa girer
    (`enforced_scopes` içinde "downstream" varsa). Üretim shadow ve assistive
    preflight'ları bu kapsamı taşır; tarihsel artifact doğrulaması için koşul
    açık tutulur.
    """
    scopes = list(payload.get("enforced_scopes") or [])
    keys = _SHA_BASE_KEYS + (
        _SHA_DOWNSTREAM_KEYS if "downstream" in scopes else ())
    missing = [k for k in keys if k not in payload]
    if missing:
        raise PreflightError("PREFLIGHT_PAYLOAD_INCOMPLETE",
                             f"eksik alanlar: {missing}")
    return _canonical_sha({k: payload[k] for k in keys})


def planned_batches(universe: Sequence[Dict[str, Any]],
                    batch_size: Optional[int] = None,
                    salts: Optional[Sequence[str]] = None) -> int:
    """Planlanan gerçek istek sayısı (iki görünüm dahil)."""
    contract = PRODUCTION_SCREENING_CONTRACT
    plans = sticky_plans(list(universe),
                         int(batch_size or contract["batch_size"]),
                         salts=list(salts or contract["view_salts"]))
    return sum(len(plan) for plan in plans)


__all__ = ["PREFLIGHT_CONTRACT_VERSION", "PreflightError", "SCREENING_MODES",
           "MODE_OFF", "MODE_SHADOW", "MODE_ASSISTIVE", "MODE_MULTIPLIERS",
           "build_assignment_preflight", "load_universe_rows",
           "preflight_sha_for", "planned_batches", "resolve_screening_mode"]
