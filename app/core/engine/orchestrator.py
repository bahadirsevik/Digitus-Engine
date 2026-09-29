"""Motor v3 Tek Koşu Orkestratörü (Faz 7).

plan_algoritma_entegrasyonu.md Faz 7 ve bağlayıcı kararlar:
  * K8: Tek koşu — evren + onaylı profil bir kez. Family V2 yalnız ADS veya SEO
    açıksa ve BİR KEZ çalışır. Yalnız SOCIAL koşusunda Family V2 çalışmaz.
  * K9: Model ve thinking kanal başına kod sabitidir (runner modülleri kendi sabitlerini kullanır).
  * K10: Sert tavan otoritesi ENGINE_V3_HARD_CAP_USD'dir. Tüm AI çağrıları downstream
    ledger ile izlenir; aşımda BudgetExceeded ile durulur.
  * K14: Motorun tek evren kaynağı run'ın dondurulmuş snapshot'ıdır (load_universe).
  * K15: Manifest mühürlenir (seal_manifest); finalize_engine_delivery güncel profille
    birebir hash eşitliğini doğrular.
  * K17: LLM aşamaları tek geçiştir (hedefli tekil tekrar ve batch tekrarı runner içindedir).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Mapping, Optional

from sqlalchemy.orm import Session

from app.core.engine.ads.runner import (
    ADS_ALGORITHM_VERSION,
    ads_models,
    ads_prompt_shas,
    run_ads_stage,
)
from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    load_confirmed_profile,
    load_universe,
)
from app.core.engine.family.runner import (
    FAMILY_ALGORITHM_VERSION,
    family_models,
    family_prompt_shas,
    run_family_stage,
)
from app.core.engine.persistence import seal_manifest
from app.core.engine.policy_gate import finalize_engine_delivery
from app.core.engine.seo.runner import (
    seo_models,
    seo_prompt_shas,
    run_seo_stage,
)
from app.core.engine.social.runner import (
    social_models,
    social_prompt_shas,
    run_social_stage,
)
from app.core.scoring.state_machine import transition
from app.database.models import ScoringRun

logger = logging.getLogger(__name__)


def run_v3_orchestration(
    db: Session,
    *,
    run: ScoringRun,
    ai: Any,
    task_id: Optional[str] = None,
    capacities: Optional[Mapping[str, int]] = None,
    log: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """Motor v3 tek koşu orkestrasyonu.

    Sırasıyla:
      1. Sürüm ve kanal kontrolleri (fail-closed).
      2. Onaylı profil ve firma bloğu (K15 hash otoritesi).
      3. Manifest mühürleme (tüm aşamaların model ve prompt SHA'ları).
      4. Dondurulmuş evren yükleme (K14).
      5. Family V2 aşaması (K8: yalnız ADS veya SEO açıksa, BİR KEZ).
      6. Seçili ADS / SEO / SOCIAL motorları (K9, K17).
      7. Post-policy kapısı ve ChannelPool teslimatı (Faz 6).
      8. channel_assigned durum geçişi ve commit.
    """
    say = log or (lambda _msg: None)

    # 1. Sürüm ve kanal kontrolleri
    version = getattr(run, "algorithm_version", None)
    if version != "v3":
        raise EngineInputError(
            f"run {run.id} algorithm_version 'v3' değil ({version!r}) — yalniz v3 orkestre edilebilir"
        )

    enable_ads = bool(getattr(run, "enable_ads", True))
    enable_seo = bool(getattr(run, "enable_seo", True))
    enable_social = bool(getattr(run, "enable_social", True))

    if not (enable_ads or enable_seo or enable_social):
        raise EngineInputError(f"run {run.id}: en az bir kanal (ADS, SEO, SOCIAL) seçili olmalıdır")

    # 2. Onaylı profil ve firma bloğu
    if not run.brand_profile_id:
        raise EngineInputError(f"run {run.id} için brand_profile_id yok")

    profile = load_confirmed_profile(db, run.brand_profile_id)
    prof_dict = build_firm_profile(profile)
    firm_text = firm_block(prof_dict)
    firm_sha = firm_block_sha256(firm_text)

    say(f"v3 orkestratör: run={run.id}, firm_sha={firm_sha[:12]}..., ADS={enable_ads}, SEO={enable_seo}, SOCIAL={enable_social}")

    # 3. Manifest mühürleme (tüm aşamaların model ve prompt SHA'ları)
    all_alg_versions: Dict[str, str] = {}
    all_models: Dict[str, str] = {}
    all_prompt_shas: Dict[str, str] = {}

    if enable_ads or enable_seo:
        all_alg_versions["family"] = FAMILY_ALGORITHM_VERSION
        all_models.update(family_models())
        all_prompt_shas.update(family_prompt_shas())

    if enable_ads:
        all_alg_versions["ads"] = ADS_ALGORITHM_VERSION
        all_models.update(ads_models())
        all_prompt_shas.update(ads_prompt_shas())

    if enable_seo:
        all_alg_versions["seo"] = "seo_v31_kati2_v2"
        all_models.update(seo_models())
        all_prompt_shas.update(seo_prompt_shas())

    if enable_social:
        all_alg_versions["social"] = "social_v5_uretim_v1"
        all_models.update(social_models())
        all_prompt_shas.update(social_prompt_shas())

    # Lokasyon politikası AI/Family çağrılarından ÖNCE mühürlenir: koşu
    # sırasında profil değişse bile motor mühürlü sözleşmeyle çalışır ve
    # finalize canlı profille karşılaştırıp fail-closed durur.
    from app.core.policy.location_policy import policy_snapshot

    seal_manifest(
        run,
        firm_block_sha256=firm_sha,
        algorithm_versions=all_alg_versions,
        models=all_models,
        prompt_shas=all_prompt_shas,
        location_policy=policy_snapshot(profile.profile_data),
    )
    db.flush()

    # 4. Dondurulmuş evren yükleme (fail-closed: snapshot execute endpoint'inde dondurulmuş olmalıdır)
    universe = load_universe(db, run.id)
    universe_rows = getattr(universe, "rows", universe)
    if not universe_rows:
        raise EngineInputError(f"run {run.id} için evren snapshot satırı bulunamadı")

    # 4b. AI/motor öncesi lokasyon filtresi (plan_v3_lokasyon_filtresi.md §5.5).
    #     MÜHÜRLÜ policy kullanılır (canlı profil DEĞİL) — mühür bu koşunun
    #     sabit sözleşmesidir. Ön-sözleşme mühür (None) -> "bu run için
    #     lokasyon sözleşmesi yok", mode none gibi davranılır; evren dokunulmaz.
    from app.core.engine.persistence import manifest_location_policy
    from app.core.policy.location_policy import MODE_NONE, evaluate_keyword

    sealed_location = manifest_location_policy(run)
    if sealed_location is not None and sealed_location.get("mode") != MODE_NONE:
        loc_mode = sealed_location["mode"]
        loc_focus = sealed_location.get("focus_cities") or []
        loc_exempt = sealed_location.get("exempt_terms") or []
        eligible_rows: List[Any] = []
        excluded_rows: List[Any] = []
        for row in universe_rows:
            decision = evaluate_keyword(
                row.keyword_text, mode=loc_mode,
                focus_cities=loc_focus, exempt_terms=loc_exempt,
            )
            (eligible_rows if decision.is_kept else excluded_rows).append(row)
        say(
            f"lokasyon filtresi ({loc_mode}): {len(excluded_rows)} kelime "
            f"AI/motor öncesi elendi, {len(eligible_rows)} kelime evrende kaldı"
        )
        if not eligible_rows:
            raise EngineInputError(
                f"run {run.id}: lokasyon filtresi ({loc_mode}) evrenin "
                "TAMAMINI eledi — hiçbir motor/AI çağrısı yapılmadı"
            )
        universe_rows = eligible_rows

    # 5. Family V2 (K8: yalnız ADS veya SEO açıksa, BİR KEZ)
    family_by_id: Optional[Dict[int, str]] = None
    families: Optional[List[Dict[str, Any]]] = None

    if enable_ads or enable_seo:
        if progress_callback:
            progress_callback(15, "Family V2: Kelime aileleri eşleştiriliyor")
        fam_res = run_family_stage(
            db,
            run=run,
            profile=prof_dict,
            rows=universe_rows,
            ai=ai,
            firm_block_sha256=firm_sha,
            log=say,
        )
        if isinstance(fam_res, dict) and "family_by_id" in fam_res:
            family_by_id = fam_res["family_by_id"]
            families = fam_res.get("families")
        elif isinstance(fam_res, dict):
            family_by_id = fam_res
            families = None
        else:
            family_by_id = None
            families = None
    else:
        say("Family V2 atlandı: Yalnız SOCIAL seçili (K8)")

    # 6. Kanal motorları çalıştırma
    channel_selections: Dict[str, List[Dict[str, Any]]] = {}

    # ADS
    if enable_ads:
        if progress_callback:
            progress_callback(40, "ADS Niche motoru çalışıyor")
        ads_res = run_ads_stage(
            db,
            run=run,
            profile=prof_dict,
            rows=universe_rows,
            family_by_id=family_by_id or {},
            ai=ai,
            firm_block_sha256=firm_sha,
            log=say,
        )
        if isinstance(ads_res, list):
            ads_cands = ads_res
        else:
            pool = ads_res.get("pool") or []
            ads_cands: List[Dict[str, Any]] = []
            for idx, item in enumerate(pool, start=1):
                kid = int(item["keyword_id"])
                ads_cands.append({
                    "keyword_id": kid,
                    "algorithm_rank": idx,
                    "scores": {
                        "Core": item.get("Core"),
                        "Selection": item.get("Selection"),
                        "MFV": item.get("MFV"),
                        "FamilyRelQ": item.get("FamilyRelQ"),
                        "Rel": item.get("Rel"),
                        "Intent": item.get("Intent"),
                    },
                    "pool_class": "primary",
                    "family_id": str(item.get("family")) if item.get("family") else None,
                    "relevance_score": item.get("Rel"),
                    "adjusted_score": item.get("Selection"),
                })
        channel_selections["ADS"] = ads_cands

    # SEO
    if enable_seo:
        if progress_callback:
            progress_callback(65, "SEO katı-2 motoru çalışıyor")
        seo_res = run_seo_stage(
            db,
            run=run,
            profile=prof_dict,
            universe=universe_rows,
            family_by_id=family_by_id or {},
            families=families or [],
            ai=ai,
            firm_block_sha256=firm_sha,
            log=say,
        )
        if isinstance(seo_res, list):
            seo_cands = seo_res
        else:
            main = seo_res.get("selection") or {}
            rows = seo_res.get("rows") or []
            by_kid = {int(r["keyword_id"]): r for r in rows}
            sel = main.get("selection") or {}
            finals = main.get("final") or {}

            seo_cands: List[Dict[str, Any]] = []
            seen_kids = set()
            current_rank = 1

            # 1. Primary candidates (algoritma seçim sırasına göre)
            for kid in sel.get("selected", []):
                kid = int(kid)
                if kid in seen_kids:
                    continue
                r = by_kid.get(kid) or {}
                rel_val = r.get("relevance") if r.get("relevance") is not None else r.get("rel")
                seo_cands.append({
                    "keyword_id": kid,
                    "algorithm_rank": current_rank,
                    "scores": {
                        "final": finals.get(kid),
                        "bp": r.get("bp"),
                        "relevance": rel_val,
                    },
                    "pool_class": "primary",
                    "family_id": str(r.get("family_id")) if r.get("family_id") else None,
                    "relevance_score": rel_val,
                    "adjusted_score": finals.get(kid),
                })
                seen_kids.add(kid)
                current_rank += 1

            # 2. Secondary candidates (aynı URL grubuna birleşenler)
            merged_dict = sel.get("merged") or {}
            for kid in sorted(merged_dict.keys()):
                kid = int(kid)
                if kid in seen_kids:
                    continue
                r = by_kid.get(kid) or {}
                rel_val = r.get("relevance") if r.get("relevance") is not None else r.get("rel")
                seo_cands.append({
                    "keyword_id": kid,
                    "algorithm_rank": current_rank,
                    "scores": {
                        "final": finals.get(kid),
                        "bp": r.get("bp"),
                        "relevance": rel_val,
                    },
                    "pool_class": "secondary",
                    "family_id": str(r.get("family_id")) if r.get("family_id") else None,
                    "relevance_score": rel_val,
                    "adjusted_score": finals.get(kid),
                })
                seen_kids.add(kid)
                current_rank += 1

            # 3. Deferred candidates (eşik veya aile sınırı)
            for deferred_item in sel.get("deferred", []):
                kid = int(deferred_item[0] if isinstance(deferred_item, (tuple, list)) else deferred_item)
                if kid in seen_kids:
                    continue
                r = by_kid.get(kid) or {}
                rel_val = r.get("relevance") if r.get("relevance") is not None else r.get("rel")
                seo_cands.append({
                    "keyword_id": kid,
                    "algorithm_rank": current_rank,
                    "scores": {
                        "final": finals.get(kid),
                        "bp": r.get("bp"),
                        "relevance": rel_val,
                    },
                    "pool_class": "deferred",
                    "family_id": str(r.get("family_id")) if r.get("family_id") else None,
                    "relevance_score": rel_val,
                    "adjusted_score": finals.get(kid),
                })
                seen_kids.add(kid)
                current_rank += 1

        channel_selections["SEO"] = seo_cands

    # SOCIAL
    if enable_social:
        if progress_callback:
            progress_callback(85, "SOCIAL V5 motoru çalışıyor")
        soc_cap = int(
            (capacities or {}).get("SOCIAL")
            or getattr(run, "social_capacity", None)
            or 30
        )
        soc_res = run_social_stage(
            db,
            run=run,
            profile=prof_dict,
            universe=universe_rows,
            ai=ai,
            firm_block_sha256=firm_sha,
            firm_block_text=firm_text,
            pool_size=soc_cap,
            log=say,
        )
        if isinstance(soc_res, list):
            soc_cands = soc_res
        else:
            result = soc_res.get("result") or {}
            kept = result.get("kept") or []
            soc_cands: List[Dict[str, Any]] = []
            for idx, row in enumerate(kept, start=1):
                kid = int(row["keyword_id"])
                rel_val = (
                    float(row["relevance_100"]) / 100.0
                    if row.get("relevance_100") is not None
                    else None
                )
                soc_cands.append({
                    "keyword_id": kid,
                    "algorithm_rank": idx,
                    "scores": {
                        "social_score": row.get("social_score"),
                        "attention": row.get("attention"),
                        "scenario": row.get("scenario"),
                        "brand_contentability": row.get("brand_contentability"),
                        "relative_fit": row.get("relative_fit"),
                    },
                    "priority": str(row.get("social_priority")) if row.get("social_priority") else None,
                    "pool_class": str(row.get("social_priority")) if row.get("social_priority") else "primary",
                    "relevance_score": rel_val,
                    "adjusted_score": row.get("social_score"),
                })
        channel_selections["SOCIAL"] = soc_cands

    # 7. Post-policy kapısı ve ChannelPool teslimatı (Faz 6)
    if progress_callback:
        progress_callback(92, "Post-policy kapısı uygulanıyor ve havuz yazılıyor")

    caps = dict(capacities or {})
    delivery_summary = finalize_engine_delivery(
        db,
        run=run,
        channel_selections=channel_selections,
        capacities=caps,
    )

    # 8. channel_assigned durum geçişi ve commit
    if run.status != "channel_assigning":
        transition(db, run, target="channel_assigning")
    transition(db, run, target="channel_assigned")
    db.commit()

    if progress_callback:
        progress_callback(100, "Kanal ataması tamamlandı")

    active_channel_names = [
        c for c, enabled in (("ADS", enable_ads), ("SEO", enable_seo), ("SOCIAL", enable_social))
        if enabled
    ]
    return {
        "status": "channel_assigned",
        "channels": active_channel_names,
        "enabled_channels": active_channel_names,
        "delivery_summary": delivery_summary,
        "total_calls": getattr(ai, "total_calls", 0),
    }
