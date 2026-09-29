"""Motor v3 Post-Policy Kapisi ve Teslimat Modulu (Faz 6).

plan_algoritma_entegrasyonu.md Faz 6 ve sozlesmeler:
  * K5: Teslim sozlesmesi `ChannelPool` tablosudur; downstream katmanlar
        (generation, export, dashboard) degismez. SEO'da yalniz PRIMARY
        satirlari `ChannelPool`'a yazilir (onayli, A2).
  * K6: Iki katman ayri kolon: `algorithm_rank` (kilidin degismeyen ciktisi)
        ve `final_rank` (policy sonrasi teslim sirasi).
  * K7: Post-policy'de GERI DOLDURMA YOKTUR, skor yeniden hesaplanmaz,
        esik gevsetilmez, IKINCI AI KARARI YOKTUR. Eksik kalirsa `unfilled_count`
        raporlanir. Teslim dilimi (ADS/SOCIAL ilk N, SEO Primary ilk N) policy
        oncesi dondurulur.
  * K15: Freshness damgalari yalniz BASARILI finalize ile AYNI transaction
         icinde yazilir: `channel_pool_policy_version`, `firm_block_sha256` (muhur uzerinden),
         `execution_manifest.firm_block_sha256`. Hata halinde kismi ChannelPool veya
         selection birakilmaz (fail-closed rollback).

Bu modul AI CAGIRMAZ. Yalnizca onayli ve deterministik policy kaynaklarini kullanir:
  1. topic_policy: onayli/dislanan temalar (approved_topic_terms + match_topic_term).
  2. competitor_policy: onayli rakip terimleri (approved_competitor_terms + match_term)
     ve kanal politikasi (competitor_policy_for).
  3. brand_defense: deterministik kendi marka korumasi (is_own_brand_keyword).
V3 onayli kanal stratejisi aramaz, okumaz ve yazmaz (ADR-004).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.core.channel.brand_defense import (
    BrandDefenseContext,
    build_brand_defense_context,
    is_own_brand_keyword,
)
from app.core.constants import COMPETITOR_TERM_REASON
from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    load_confirmed_profile,
    load_universe,
)
from app.core.engine.persistence import (
    manifest_firm_block_sha,
    manifest_location_policy,
    write_engine_selections,
)
from app.core.policy.competitor_policy import (
    POLICY_BLOCK,
    approved_competitor_terms,
    competitor_policy_for,
    match_term,
)
from app.core.policy.location_policy import (
    CITY_LEXICON_VERSION,
    MODE_NONE,
    evaluate_keyword,
    policy_snapshot,
)
from app.core.policy.topic_policy import (
    approved_topic_terms,
    match_topic_term,
)
from app.database.models import ChannelPool, EngineSelection, ScoringRun

logger = logging.getLogger(__name__)

# Exclude reason sabitleri
EXCLUDE_REASON_COMPETITOR = COMPETITOR_TERM_REASON  # "COMPETITOR_TERM"
EXCLUDE_REASON_TOPIC = "TOPIC_EXCLUDED"
EXCLUDE_REASON_CAPACITY_LIMIT = "CAPACITY_LIMIT"
EXCLUDE_REASON_SEO_NOT_PRIMARY = "SEO_NOT_PRIMARY"

CHANNELS = ("ADS", "SEO", "SOCIAL")


def _location_filter_summary(
    universe: Any, sealed_location: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Run özetine eklenecek lokasyon filtresi kırılımı (plan §5.6).

    Dondurulmuş TAM evren (`load_universe`) üzerinden hesaplanır — yalnız
    `channel_selections`'a giren adaylar değil: pre-AI eleme (orchestrator
    Faz 5.5) bu satırları kanal motorlarına hiç göndermemiş olabilir; özet
    yine de lokasyon yüzünden "hiç aday olmayan" satırları doğru raporlamalıdır.
    Tam keyword metinleri TAŞINMAZ — yalnız sayım (mod/neden/şehir kırılımı).
    """
    mode = (sealed_location or {}).get("mode") or MODE_NONE
    lexicon_version = (
        (sealed_location or {}).get("city_lexicon_version") or CITY_LEXICON_VERSION
    )
    enabled = sealed_location is not None and mode != MODE_NONE

    excluded_by_reason: Dict[str, int] = {}
    excluded_by_city: Dict[str, int] = {}
    excluded_count = 0

    if enabled:
        focus_cities = sealed_location.get("focus_cities") or []
        exempt_terms = sealed_location.get("exempt_terms") or []
        for row in universe.rows:
            decision = evaluate_keyword(
                row.keyword_text, mode=mode,
                focus_cities=focus_cities, exempt_terms=exempt_terms,
            )
            if decision.is_kept:
                continue
            excluded_count += 1
            reason_code = (decision.exclude_reason or "").split(":", 1)[0]
            if reason_code:
                excluded_by_reason[reason_code] = excluded_by_reason.get(reason_code, 0) + 1
            if decision.matched_city:
                excluded_by_city[decision.matched_city] = (
                    excluded_by_city.get(decision.matched_city, 0) + 1
                )

    return {
        "enabled": enabled,
        "mode": mode,
        "lexicon_version": lexicon_version,
        "excluded_count": excluded_count,
        "excluded_by_reason": excluded_by_reason,
        "excluded_by_city": excluded_by_city,
    }


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Tekil bir aday icin policy degerlendirmesi."""
    is_kept: bool
    exclude_reason: Optional[str] = None


def _first_not_none(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def evaluate_policy_candidate(
    keyword_text: str,
    channel: str,
    *,
    topic_terms: Sequence[str],
    competitor_terms: Sequence[str],
    competitor_blocked: bool,
    brand_ctx: Optional[BrandDefenseContext],
    location_policy: Optional[Mapping[str, Any]] = None,
) -> PolicyEvaluationResult:
    """Bir kelimeyi onayli ve deterministik politikalara karsi degerlendirir.

    Sirasiyla:
      0. Lokasyon ikinci savunma hatti (backstop, plan §5.5 son paragraf):
         MUHURLU lokasyon politikasi burada AYNI matcher ile tekrar calisir.
         Pre-AI eleme (orchestrator) bu adaylari zaten evrenden cikarmis
         olmalidir; bu adim yalniz "bir sekilde buraya kadar geldiyse"
         durumunu yakalar. GERI DOLDURMA YAPMAZ, IKINCI AI KARARI URETMEZ —
         yalniz dislar.
      1. Deterministik kendi-marka korumasi:
         Eger is_own_brand_keyword True ise (itibar/negatif sinyal tasimayan oz marka),
         rakip engeline takilmasi engellenir.
      2. Topic policy: Onayli dislanan temalarla token sinirli eslesirse ELENIR.
      3. Competitor policy: Ilgili kanalda block aktifse ve onayli rakip terimi
         iceriyorsa (ve kendi-marka korumasi yoksa) ELENIR.
      4. Hicbirine takilmazsa: KORUNUR (is_kept=True).
    """
    channel_upper = channel.upper()

    if location_policy is not None:
        loc_mode = location_policy.get("mode")
        if loc_mode and loc_mode != MODE_NONE:
            loc_decision = evaluate_keyword(
                keyword_text, mode=loc_mode,
                focus_cities=location_policy.get("focus_cities") or [],
                exempt_terms=location_policy.get("exempt_terms") or [],
            )
            if not loc_decision.is_kept:
                return PolicyEvaluationResult(
                    is_kept=False, exclude_reason=loc_decision.exclude_reason)

    is_own = is_own_brand_keyword(keyword_text, brand_ctx, channel_upper)

    # 1. Konu politikasi (dislanan konular / temalar)
    if topic_terms:
        matched_topic = match_topic_term(keyword_text, list(topic_terms))
        if matched_topic:
            reason = f"{EXCLUDE_REASON_TOPIC}:{matched_topic}"[:60]
            return PolicyEvaluationResult(is_kept=False, exclude_reason=reason)

    # 2. Rakip politikasi (yalniz kanal blok modundaysa ve kendi-marka degilse)
    if competitor_blocked and competitor_terms and not is_own:
        matched_comp = match_term(keyword_text, list(competitor_terms))
        if matched_comp:
            reason = f"{EXCLUDE_REASON_COMPETITOR}:{matched_comp}"[:60]
            return PolicyEvaluationResult(is_kept=False, exclude_reason=reason)

    return PolicyEvaluationResult(is_kept=True, exclude_reason=None)


def process_channel_candidates(
    raw_candidates: Sequence[Mapping[str, Any]],
    channel: str,
    *,
    channel_cap: int,
    canonical_universe_map: Mapping[int, str],
    topic_terms: Sequence[str],
    competitor_terms: Sequence[str],
    competitor_blocked: bool,
    brand_ctx: Optional[BrandDefenseContext],
    policy_version: int,
    location_policy: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Bir kanalin adaylarini dogrular, dilimler, policy kapisindan gecirir ve final_rank sikistirir.

    K7 No-backfill kurali:
      - ADS & SOCIAL: Teslim dilimi algorithm_rank sirasina dizilmis adaylarin ilk capacity elemanidir.
        Kapasite disi kalan adaylar policy'ye girmeden CAPACITY_LIMIT ile elenir (final_rank=None).
      - SEO: Teslim dilimi Primary havuzunun ilk capacity elemanidir (pool_class == 'primary').
        Primary olmayan adaylar SEO_NOT_PRIMARY ile, kapasiteyi asan primary adaylar
        CAPACITY_LIMIT ile elenir (final_rank=None).
      - Policy elenmesi sonrasinda dilim disindan ASLA geri doldurma (backfill) yapilmaz.
      - Kalanlarin final_rank degerleri goreli siralari bozulmadan 1..M seklinde sikistirilir.
    """
    seen_ids: Set[int] = set()
    seen_ranks: Set[int] = set()

    for c in raw_candidates:
        raw_kid = c.get("keyword_id")
        if raw_kid is None:
            raise EngineInputError(f"Kanal {channel}: keyword_id eksik")
        try:
            kid = int(raw_kid)
        except (ValueError, TypeError):
            raise EngineInputError(f"Kanal {channel}: gecersiz keyword_id {raw_kid!r}")

        if kid not in canonical_universe_map:
            raise EngineInputError(f"Kanal {channel}: keyword_id {kid} run evreninde yok")

        if kid in seen_ids:
            raise EngineInputError(f"Kanal {channel}: tekrarlı keyword_id {kid} tespit edildi")
        seen_ids.add(kid)

        alg_rank = c.get("algorithm_rank")
        if alg_rank is None or not isinstance(alg_rank, int) or alg_rank <= 0:
            raise EngineInputError(
                f"Kanal {channel}: keyword_id {kid} algorithm_rank pozitif tamsayi olmalidir (gelen: {alg_rank!r})"
            )
        if alg_rank in seen_ranks:
            raise EngineInputError(f"Kanal {channel}: tekrarlı algorithm_rank {alg_rank} tespit edildi")
        seen_ranks.add(alg_rank)

    sorted_candidates = sorted(raw_candidates, key=lambda c: int(c["algorithm_rank"]))

    in_slice: List[Mapping[str, Any]] = []
    pre_excluded: List[Tuple[Mapping[str, Any], str]] = []

    if channel in ("ADS", "SOCIAL"):
        in_slice = sorted_candidates[:channel_cap]
        for c in sorted_candidates[channel_cap:]:
            pre_excluded.append((c, EXCLUDE_REASON_CAPACITY_LIMIT))
    elif channel == "SEO":
        primary_candidates: List[Mapping[str, Any]] = []
        for c in sorted_candidates:
            if c.get("pool_class") == "primary":
                primary_candidates.append(c)
            else:
                pre_excluded.append((c, EXCLUDE_REASON_SEO_NOT_PRIMARY))

        in_slice = primary_candidates[:channel_cap]
        for c in primary_candidates[channel_cap:]:
            pre_excluded.append((c, EXCLUDE_REASON_CAPACITY_LIMIT))

    all_processed: List[Dict[str, Any]] = []
    pool_items: List[Dict[str, Any]] = []

    next_final_rank = 1

    # 1. Teslim dilimi icindekilerin policy degerlendirmesi
    for item in in_slice:
        kid = int(item["keyword_id"])
        canonical_text = canonical_universe_map[kid]

        eval_res = evaluate_policy_candidate(
            canonical_text,
            channel,
            topic_terms=topic_terms,
            competitor_terms=competitor_terms,
            competitor_blocked=competitor_blocked,
            brand_ctx=brand_ctx,
            location_policy=location_policy,
        )

        if eval_res.is_kept:
            final_rank = next_final_rank
            next_final_rank += 1
            exclude_reason = None
        else:
            final_rank = None
            exclude_reason = eval_res.exclude_reason or "EXCLUDED"

        rel_val = _first_not_none(
            item.get("relevance_score"),
            item.get("Rel"),
            item.get("relevance"),
        )
        adj_val = _first_not_none(
            item.get("adjusted_score"),
            item.get("Selection"),
            item.get("final"),
            item.get("social_score"),
        )

        scores = dict(item.get("scores") or {}) if item.get("scores") else None

        processed_entry = {
            "keyword_id": kid,
            "keyword_text": canonical_text,
            "channel": channel.upper(),
            "algorithm_rank": int(item["algorithm_rank"]),
            "scores": scores,
            "pool_class": item.get("pool_class"),
            "family_id": str(item["family_id"]) if item.get("family_id") is not None else None,
            "priority": str(item["priority"]) if item.get("priority") is not None else None,
            "final_rank": final_rank,
            "exclude_reason": exclude_reason,
            "policy_version": policy_version,
            "relevance_score": rel_val,
            "adjusted_score": adj_val,
            "pool_label": item.get("pool_label"),
        }
        all_processed.append(processed_entry)
        if final_rank is not None:
            pool_items.append(processed_entry)

    # 2. Dilim disi adaylar (policy'ye girmeden elenenler)
    for item, reason in pre_excluded:
        kid = int(item["keyword_id"])
        canonical_text = canonical_universe_map[kid]

        rel_val = _first_not_none(
            item.get("relevance_score"),
            item.get("Rel"),
            item.get("relevance"),
        )
        adj_val = _first_not_none(
            item.get("adjusted_score"),
            item.get("Selection"),
            item.get("final"),
            item.get("social_score"),
        )
        scores = dict(item.get("scores") or {}) if item.get("scores") else None

        processed_entry = {
            "keyword_id": kid,
            "keyword_text": canonical_text,
            "channel": channel.upper(),
            "algorithm_rank": int(item["algorithm_rank"]),
            "scores": scores,
            "pool_class": item.get("pool_class"),
            "family_id": str(item["family_id"]) if item.get("family_id") is not None else None,
            "priority": str(item["priority"]) if item.get("priority") is not None else None,
            "final_rank": None,
            "exclude_reason": reason,
            "policy_version": policy_version,
            "relevance_score": rel_val,
            "adjusted_score": adj_val,
            "pool_label": item.get("pool_label"),
        }
        all_processed.append(processed_entry)

    # Tum adaylari orijinal algorithm_rank sirasina gore diz
    all_processed.sort(key=lambda c: int(c["algorithm_rank"]))
    return all_processed, pool_items


def finalize_engine_delivery(
    db: Session,
    *,
    run: ScoringRun,
    channel_selections: Mapping[str, Sequence[Mapping[str, Any]]],
    capacities: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """Motor v3 teslimat adimi: Post-policy kapisi + ChannelPool ve EngineSelection yazimi.

    Tek transaction icinde atomik calisir (K15):
      1. Yalniz algorithm_version == "v3" run'lar finalize edilebilir.
      2. Beklenen kanal kumesi run'in enable_* alanlarindan cikarilir ve
         channel_selections ile birebir eslesmelidir. Eksik, fazla veya
         cakisan anahtarlar fail-closed EngineInputError verir.
      3. Onayli profil yuklenir. Muhurlu firm_block_sha256 guncel profile karsi dogrulanir;
         farkliysa fail-closed durulur (0 satir yazilir).
      4. load_universe ile donmus evren yuklenir, her aday ID'sinin evrende oldugu ve
         snapshot'taki kanonik metni kullanacagi dogrulanir.
      5. K7 no-backfill kuralina gore teslim dilimleri dondurulup policy uygulanir,
         final_rank sikistirilir.
      6. EngineSelection ve ChannelPool yazilir.
      7. K15 Freshness damgalari ayni transaction icinde yazilir (muhur degistirilmez).
      8. Hata durumunda rollback yapilir ve kismi kayit birakilmaz.
    """
    version = getattr(run, "algorithm_version", None)
    if version != "v3":
        raise EngineInputError(
            f"run {run.id} algorithm_version 'v3' degil ({version!r}) — yalniz v3 finalize edilebilir"
        )

    # Beklenen kanal kumesi: run'in enable_* alanlarindan cikarilir
    expected_channels: Set[str] = set()
    if getattr(run, "enable_ads", True):
        expected_channels.add("ADS")
    if getattr(run, "enable_seo", True):
        expected_channels.add("SEO")
    if getattr(run, "enable_social", True):
        expected_channels.add("SOCIAL")

    # channel_selections normalize edilir ve cakisma kontrolu yapilir
    normalized_selections: Dict[str, Sequence[Mapping[str, Any]]] = {}
    for raw_key, raw_candidates in channel_selections.items():
        norm_key = str(raw_key).strip().upper()
        if norm_key in normalized_selections:
            raise EngineInputError(
                f"channel_selections icinde normalize edilince cakisan anahtar tespit edildi: "
                f"{raw_key!r} (mevcut: {norm_key!r})"
            )
        normalized_selections[norm_key] = raw_candidates

    received_channels = set(normalized_selections.keys())
    missing_channels = expected_channels - received_channels
    extra_channels = received_channels - expected_channels

    if missing_channels:
        raise EngineInputError(
            f"Beklenen acik kanallar eksik: {sorted(missing_channels)} "
            f"(beklenen: {sorted(expected_channels)}, gelen: {sorted(received_channels)})"
        )
    if extra_channels:
        raise EngineInputError(
            f"Kapali veya tanimsiz kanallar gonderildi: {sorted(extra_channels)} "
            f"(beklenen: {sorted(expected_channels)}, gelen: {sorted(received_channels)})"
        )

    if not run.brand_profile_id:
        raise EngineInputError(f"run {run.id} icin brand_profile_id yok")

    profile = load_confirmed_profile(db, run.brand_profile_id)
    profile_data = profile.profile_data if isinstance(profile.profile_data, dict) else {}

    # Muhurlu hash kontrolu (K15: muhur degismezdir)
    sealed_firm_sha = manifest_firm_block_sha(run)
    prof_dict = build_firm_profile(profile)
    current_firm_block = firm_block(prof_dict)
    current_firm_sha = firm_block_sha256(current_firm_block)

    if sealed_firm_sha != current_firm_sha:
        raise EngineInputError(
            f"run {run.id} profil muhurden sonra degismis — teslimat reddedildi "
            f"(muhur={sealed_firm_sha[:12]}..., guncel={current_firm_sha[:12]}...)"
        )

    # Lokasyon politikasi fail-closed kontrolu (plan §5.4). ChannelPool/
    # EngineSelection yazimindan ONCE calisir: uyusmazlikta KISMI teslimat
    # olusmaz. Eski sema (surum 1) YALNIZ canli filtre kapaliyken kabul
    # edilir — yeni bir kullanici tercihi eski kosuya sessizce uygulanmaz.
    sealed_location = manifest_location_policy(run)
    live_location = policy_snapshot(profile_data)
    if sealed_location is None:
        if live_location["mode"] != MODE_NONE:
            raise EngineInputError(
                f"run {run.id} lokasyon sozlesmesinden ONCE muhurlenmis ancak "
                f"profil filtresi acilmis ({live_location['mode']}) — eski kosu "
                "finalize edilmez, yeni kosu gerekir"
            )
    elif (sealed_location.get("enforcement_fingerprint")
            != live_location["enforcement_fingerprint"]):
        raise EngineInputError(
            f"run {run.id} lokasyon politikasi muhurden sonra degismis — "
            f"teslimat reddedildi (muhur mod={sealed_location.get('mode')}, "
            f"guncel mod={live_location['mode']})"
        )

    policy_version = int(getattr(profile, "policy_version", 1) or 1)
    anchor_version = int(getattr(profile, "anchor_version", 1) or 1)

    # Donmus evreni yukle
    universe = load_universe(db, run.id)
    canonical_universe_map: Dict[int, str] = {
        row.keyword_id: row.keyword_text for row in universe.rows
    }

    # Run ozeti icin lokasyon filtresi kirilimi (plan §5.6). TAM evren
    # uzerinden hesaplanir: pre-AI eleme (orchestrator) bazi satirlari kanal
    # motorlarina hic gondermemis olabilir, ozet yine de dogru saymalidir.
    location_filter_summary = _location_filter_summary(universe, sealed_location)

    # Politika kaynaklari
    topic_terms = approved_topic_terms(profile)
    competitor_terms = approved_competitor_terms(profile)
    comp_policy = competitor_policy_for(profile)
    brand_ctx = build_brand_defense_context(
        profile_data.get("brand_terms"),
        profile_data.get("company_name"),
    )

    caps = dict(capacities or {})
    processed_by_channel: Dict[str, List[Dict[str, Any]]] = {}
    pool_candidates_by_channel: Dict[str, List[Dict[str, Any]]] = {}

    for channel in sorted(expected_channels):
        raw_candidates = normalized_selections[channel]

        channel_cap = caps.get(channel)
        if channel_cap is None:
            if channel == "ADS":
                channel_cap = int(getattr(run, "ads_capacity", None) or 60)
            elif channel == "SEO":
                channel_cap = int(getattr(run, "seo_capacity", None) or 30)
            elif channel == "SOCIAL":
                channel_cap = int(getattr(run, "social_capacity", None) or 30)
            else:
                channel_cap = 60

        comp_blocked = comp_policy.get(channel.lower()) == POLICY_BLOCK

        all_proc, pool_items = process_channel_candidates(
            raw_candidates,
            channel,
            channel_cap=channel_cap,
            canonical_universe_map=canonical_universe_map,
            topic_terms=topic_terms,
            competitor_terms=competitor_terms,
            competitor_blocked=comp_blocked,
            brand_ctx=brand_ctx,
            policy_version=policy_version,
            location_policy=sealed_location,
        )
        processed_by_channel[channel] = all_proc
        pool_candidates_by_channel[channel] = pool_items

    # Dual-channel etiketleme: ADS ∩ SEO kesisimi
    ads_pool_kids: Set[int] = {
        item["keyword_id"] for item in pool_candidates_by_channel.get("ADS", [])
    }
    seo_pool_kids: Set[int] = {
        item["keyword_id"] for item in pool_candidates_by_channel.get("SEO", [])
    }
    strategic_kids: Set[int] = ads_pool_kids & seo_pool_kids

    # ── Atomik Yazma (Fail-Closed) ──────────────────────────────────
    try:
        # Once ayni run icin mevcut EngineSelection ve ChannelPool temizlenir
        db.query(ChannelPool).filter(ChannelPool.scoring_run_id == run.id).delete(
            synchronize_session=False
        )
        db.query(EngineSelection).filter(EngineSelection.scoring_run_id == run.id).delete(
            synchronize_session=False
        )

        # 1. EngineSelection satirlari (write_engine_selections XOR kontrolunu de yapar)
        all_selections: List[Dict[str, Any]] = []
        for channel in sorted(expected_channels):
            all_selections.extend(processed_by_channel.get(channel, []))
        write_engine_selections(db, scoring_run_id=run.id, selections=all_selections)

        # 2. ChannelPool satirlari
        for channel in sorted(expected_channels):
            items = pool_candidates_by_channel.get(channel, [])
            for item in items:
                is_strat = item["keyword_id"] in strategic_kids and channel in ("ADS", "SEO")

                rel_val = item.get("relevance_score")
                rel_dec = Decimal(str(round(float(rel_val), 3))) if rel_val is not None else None

                adj_val = item.get("adjusted_score")
                adj_dec = Decimal(str(round(float(adj_val), 4))) if adj_val is not None else None

                db.add(ChannelPool(
                    scoring_run_id=run.id,
                    keyword_id=item["keyword_id"],
                    channel=channel,
                    final_rank=item["final_rank"],
                    relevance_score=rel_dec,
                    adjusted_score=adj_dec,
                    is_strategic=is_strat,
                    pool_label=item.get("pool_label"),
                ))

        # 3. K15 Freshness damgalari (muhure DOKUNULMAZ, muhur degismezdir)
        run.channel_pool_policy_version = policy_version
        run.relevance_anchor_version = anchor_version

        db.flush()
    except Exception as exc:
        db.rollback()
        logger.error(f"Motor v3 finalize teslimatinda hata, rollback yapildi: {exc}")
        raise

    # ── Summary / Raporlama ──────────────────────────────────────────
    summary: Dict[str, Any] = {
        "scoring_run_id": run.id,
        "policy_version": policy_version,
        "firm_block_sha256": current_firm_sha,
        "strategic_keywords_count": len(strategic_kids),
        "location_filter": location_filter_summary,
        "channels": {},
    }

    for channel in sorted(expected_channels):
        all_items = processed_by_channel[channel]
        pool_items = pool_candidates_by_channel.get(channel, [])
        excluded_items = [it for it in all_items if it["final_rank"] is None]
        kept_items = [it for it in all_items if it["final_rank"] is not None]

        channel_cap = caps.get(channel)
        if channel_cap is None:
            if channel == "ADS":
                channel_cap = int(getattr(run, "ads_capacity", None) or 60)
            elif channel == "SEO":
                channel_cap = int(getattr(run, "seo_capacity", None) or 30)
            elif channel == "SOCIAL":
                channel_cap = int(getattr(run, "social_capacity", None) or 30)
            else:
                channel_cap = 60

        unfilled = max(0, channel_cap - len(pool_items))

        summary["channels"][channel] = {
            "total_candidates": len(all_items),
            "kept_count": len(kept_items),
            "excluded_count": len(excluded_items),
            "pool_count": len(pool_items),
            "capacity": channel_cap,
            "unfilled_count": unfilled,
        }

    return summary
