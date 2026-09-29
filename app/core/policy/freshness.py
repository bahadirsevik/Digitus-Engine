"""ChannelPool freshness — merkezi tanım (plan v13 + v2.1 Faz C + ADR-004).

Freshness eksenleri:
- policy_stale: havuz, workspace'in güncel rakip/konu politikasıyla mı üretildi?
- relevance_stale:
  - v3: deterministik firm_block SHA256 karşılaştırması (K15 / ADR-004).
  - legacy v2/v2_1: anchor_version karşılaştırması.
- strategy_stale: YALNIZCA legacy algorithm_version='v2_1' run'larda havuz, güncel
  ONAYLI kanal stratejisiyle mi üretildi? v2 ve v3 run'lar stratejiden bağımsızdır
  ve bu eksen yüzünden ASLA bayatlamaz (v3'te daima False, ADR-004).
- screening_context_stale: YALNIZ tarihsel assistive havuzlar bu eksende bayatlar;
  off/shadow havuzları tarama bağlamından etkilenmez.
- assignment_in_progress: Atama sürerken eski havuz generation/export için stale kabul edilir.

V3 freshness otoriteleri:
  1. policy_version (channel_pool_policy_version != workspace.policy_version)
  2. firm_block_sha256 (manifest_firm_block_sha(run) != current_firm_sha)
  3. assignment_in_progress (durum: 'channel_assigning')
  4. yalnız tarihsel assistive havuzlarda screening_context_stale

Karşılaştırma `!=` iledir (BİLİNÇLİ — v13 invariant #1): `<` kullanmak,
restore/manuel düzeltme/hatalı backfill sonrası run sürümü workspace'ten BÜYÜK
kaldığında yanlış "fresh" üretirdi. Eşit olmayan her şey stale'dir.

Bu modül tek hesap noktasıdır: guard'lar (generation/export/regenerate),
pools/dashboard yanıtları ve UI bandı hep buradan beslenir.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

# Atama surerken eski havuz "guncel" gosterilmez (plan §7.4)
RUN_STATUS_ASSIGNING = "channel_assigning"


@dataclass(frozen=True)
class PoolFreshness:
    policy_stale: bool
    relevance_stale: bool
    strategy_stale: bool = False
    # plan §7.4: YALNIZ assistive havuzlar bu eksende bayatlar; off/shadow
    # havuzlari tarama baglamindan ETKILENMEZ (screening izi yok)
    screening_context_stale: bool = False
    # Atama surerken eski havuz generation/export icin stale kabul edilir.
    assignment_in_progress: bool = False

    @property
    def channel_pool_stale(self) -> bool:
        return (self.policy_stale or self.relevance_stale
                or self.strategy_stale or self.screening_context_stale
                or self.assignment_in_progress)

    def as_dict(self) -> dict:
        return {
            "channel_pool_stale": self.channel_pool_stale,
            "policy_stale": self.policy_stale,
            "relevance_stale": self.relevance_stale,
            "strategy_stale": self.strategy_stale,
            "screening_context_stale": self.screening_context_stale,
            "assignment_in_progress": self.assignment_in_progress,
        }


def compute_pool_freshness(run, workspace) -> PoolFreshness:
    """run: ScoringRun, workspace: BrandProfile (veya None).

    Workspace'siz run (legacy global havuz) sürümlenemez → fresh kabul edilir
    (politika da workspace'e bağlı olduğundan engellenecek bir şey yoktur).
    """
    if workspace is None:
        return PoolFreshness(policy_stale=False, relevance_stale=False)

    is_v3 = getattr(run, "algorithm_version", "v2") == "v3"
    if is_v3:
        # K15: v3 freshness sözleşmesi
        # 1. policy_stale: güncel workspace.policy_version ile != karşılaştırması
        policy_stale = (
            run.channel_pool_policy_version is None
            or run.channel_pool_policy_version != workspace.policy_version
        )
        # 2. strategy_stale: v3 için kanal stratejisi bir güncellik ekseni değildir (V3-only kararı)
        strategy_stale = False
        # 3. relevance_stale: otorite relevance_anchor_version DEĞİLDİR;
        # deterministik firm_block metninin SHA256'sıdır. Güncel onaylı profilden
        # yeniden üretilen hash ile karşılaştırılır.
        # V3 freshness YALNIZ mühürlü nested hash'i (engine_v3.firm_block_sha256) otorite kabul eder.
        from app.core.engine.context import (
            build_firm_profile,
            firm_block,
            firm_block_sha256,
        )
        from app.core.engine.persistence import manifest_firm_block_sha
        try:
            run_firm_sha = manifest_firm_block_sha(run)
        except Exception:
            run_firm_sha = None

        current_firm_sha = None
        if getattr(workspace, "status", None) == "confirmed" and not getattr(workspace, "deleted_at", None):
            try:
                prof_dict = build_firm_profile(workspace)
                current_firm_sha = firm_block_sha256(firm_block(prof_dict))
            except Exception:
                current_firm_sha = None

        relevance_stale = (
            run_firm_sha is None
            or current_firm_sha is None
            or run_firm_sha != current_firm_sha
        )
    else:
        policy_stale = (
            run.channel_pool_policy_version is None
            or run.channel_pool_policy_version != workspace.policy_version
        )
        relevance_required = bool(
            getattr(settings, "ENABLE_RELEVANCE_RERANK", False)
            and not run.skip_relevance
        )
        relevance_stale = relevance_required and (
            run.relevance_anchor_version is None
            or run.relevance_anchor_version != workspace.anchor_version
        )
        # v2.1: strateji ekseni yalnız v2_1 run'larda değerlendirilir (plan §6);
        # karşılaştırma `!=` iledir (v13 invariant #1 ile aynı gerekçe).
        strategy_stale = False
        if getattr(run, "algorithm_version", "v2") == "v2_1":
            pool_version = getattr(run, "channel_pool_strategy_version", None)
            strategy_stale = (
                pool_version is None
                or pool_version != int(getattr(workspace, "strategy_version", 0) or 0)
            )
    # plan §7.4 "Tarama bağlamı değişti": son BAŞARILI havuz assistive ise
    # canlı canonical screening context SHA'sı ile karşılaştırılır. Bağlam
    # artık kurulamıyorsa (onaylı strateji kaldırıldı) havuz DOĞRULANAMAZ →
    # fail-closed olarak stale sayılır.
    screening_context_stale = False
    if getattr(run, "channel_pool_screening_mode", None) == "assistive":
        from app.core.screening.context import (
            ScreeningContextMissing,
            canonical_screening_context,
        )

        pool_sha = getattr(run, "channel_pool_screening_context_sha256", None)
        try:
            live_sha = canonical_screening_context(workspace)["context_sha256"]
        except ScreeningContextMissing:
            live_sha = None
        screening_context_stale = (pool_sha is None or live_sha is None
                                   or pool_sha != live_sha)
    return PoolFreshness(
        policy_stale=policy_stale,
        relevance_stale=relevance_stale,
        strategy_stale=strategy_stale,
        screening_context_stale=screening_context_stale,
        assignment_in_progress=(
            getattr(run, "status", None) == RUN_STATUS_ASSIGNING),
    )


def is_export_policy_outdated(db, job) -> bool:
    """Tamamlanmis bir ExportJob'un 'eski politikayla uretildi' etiketi.

    TEK kaynak (plan v4 tur-6 #2): export status API'si VE dashboard ozeti
    ayni hesabi kullanir. NULL snapshot (migration oncesi kayit) da outdated
    sayilir — guncel rapor sanilmasin. Dosya yine INDIRILEBILIR kalir.
    """
    if job.status != "completed":
        return False
    from app.database.models import BrandProfile

    workspace = (
        db.query(BrandProfile).filter(BrandProfile.id == job.brand_profile_id).first()
    )
    if workspace is None:
        return False
    return (
        job.requested_policy_version is None
        or job.requested_policy_version != (workspace.policy_version or 1)
        or job.requested_anchor_version is None
        or job.requested_anchor_version != (workspace.anchor_version or 1)
    )
