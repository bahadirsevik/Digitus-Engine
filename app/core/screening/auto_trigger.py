# -*- coding: utf-8 -*-
"""Sunucu kontrollü tarama kararı (revize plan §3.1/§3.2).

Kullanıcı YALNIZ kanal başına kelime sayısını seçer. Mod, sağlayıcı,
kimlik ve maliyet tavanı arayüzde GÖRÜNMEZ; scoring bittikten sonra
atama dispatch'inde sunucu karar verir:

    kill switch → allowlist → varsayılan/operatör modu → algoritma
    sürümü → onaylı strateji

İlk başarısız kapı BASELINE'a düşürür ve **sebep kaydedilir**
(`attempt.manifest.screening_skipped_reason` + parent task result_data).
Hiçbir kapı kullanıcıya HATA olarak dönmez — kullanıcı yalnız normal
atamasını görür.

Kapı 4-8 (bağlam, evren limiti, relevance tazeliği, screening cap,
downstream cap) preflight'ta yaşar; orada tipli `PreflightError` üretilir
ve otomatik akışta yine sessiz baseline'a çevrilir (dispatcher).
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.core.screening.preflight import (
    MODE_ASSISTIVE,
    MODE_OFF,
    MODE_SHADOW,
)

SERVER_MODES = (MODE_SHADOW, MODE_ASSISTIVE)
# Run bazlı tercih yönetici varsayılanını AŞAMAZ (yalnız daraltabilir)
MODE_POWER = {MODE_OFF: 0, MODE_SHADOW: 1, MODE_ASSISTIVE: 2}
SUPPORTED_ALGORITHM_VERSIONS = ("v2", "v2_1")
CANDIDATE_CANARY_CONTRACT_VERSION = "CANDIDATE-ROLLOUT-2026-08-03-v1"
CANDIDATE_CANARY_MAX_WORKSPACES = 2
CANDIDATE_CANARY_RUNS_PER_WORKSPACE = 3


def allowlisted(workspace, settings) -> bool:
    """Workspace tarama allowlist'inde mi? (boş liste = HİÇBİRİ)."""
    if workspace is None:
        return False
    allow = getattr(settings, "CORPUS_SCREENING_ALLOWLIST", None) or []
    try:
        return int(workspace.id) in {int(x) for x in allow}
    except (TypeError, ValueError):
        return False


def assistive_allowed(workspace, settings) -> bool:
    """Assistive YALNIZ bayrak açık + allowlist'li workspace'te açılır.

    Assistive canlı seçimi değiştirir: yanlışlıkla (ör. run tercihi eski
    bir denemeden kalmışsa) açılmamalıdır — fail-closed.
    """
    if not getattr(settings, "ENABLE_CORPUS_SCREENING", False):
        return False
    return allowlisted(workspace, settings)


def candidate_canary_run_count(db, workspace) -> int:
    """Bu canary sozlesmesiyle baslatilmis assistive denemeleri say.

    Eski shadow/validation denemeleri canary kotasini tuketmez. Basarisiz
    assistive denemeler de sayilir; saglayici cagrisi yapmis olabilecek bir
    deneme yeniden ucretsizmis gibi acilamaz.
    """
    if db is None or workspace is None:
        return 0

    from app.database.models import ChannelAssignmentAttempt

    attempts = (
        db.query(ChannelAssignmentAttempt)
        .filter(
            ChannelAssignmentAttempt.brand_profile_id == int(workspace.id),
            ChannelAssignmentAttempt.screening_mode == MODE_ASSISTIVE,
        )
        .all()
    )
    return sum(
        (row.manifest or {}).get("candidate_canary_contract_version")
        == CANDIDATE_CANARY_CONTRACT_VERSION
        for row in attempts
    )


def decide_screening_mode(
    run, workspace, *, settings=None, db=None
) -> Tuple[str, Optional[str]]:
    """Sunucunun mod kararı. Dönüş: `(mode, skip_reason)`.

    `skip_reason` yalnız mod `off`a düştüğünde doludur ve denetime yazılır.
    """
    from app.config import settings as default_settings

    settings = settings or default_settings

    if not getattr(settings, "ENABLE_CORPUS_SCREENING", False):
        return MODE_OFF, "KILL_SWITCH_OFF"
    if workspace is None:
        return MODE_OFF, "WORKSPACE_MISSING"
    if not allowlisted(workspace, settings):
        return MODE_OFF, "WORKSPACE_NOT_ALLOWLISTED"

    # Tarama union'ı YALNIZ ADS/SEO'ya uygulanır (`assistive.APPLIED_CHANNELS`).
    # Kullanıcı SOCIAL-only bir koşu başlattığında (ör. "mevcut SOCIAL"
    # baseline'ı) taramanın uygulanabileceği tek bir kanal yoktur; yine de
    # tetiklenirse tüm evren için ÜCRETLİ sağlayıcı çağrısı yapılır ve
    # sonucu hiçbir yere yazılamaz. Bu kapı o harcamayı kapatır.
    from app.core.screening.assistive import expected_applied_channels

    if not expected_applied_channels(run):
        return MODE_OFF, "NO_SCREENABLE_CHANNEL_ACTIVE"

    # Operatör run bazlı override edebilir (API'de gizli alan) AMA
    # yönetici varsayılanını AŞAMAZ. Aksi halde `DEFAULT_MODE=off`
    # (harcamayı durdurma kolu) eski bir run satırındaki tercih yüzünden
    # sessizce etkisiz kalırdı — canlı doğrulamada yakalandı: benchmark
    # scripti run 26'ya `assistive` yazmıştı ve varsayılan `off` iken bile
    # o run assistive dönüyordu. Override yalnız DARALTABİLİR.
    admin_default = str(
        getattr(settings, "CORPUS_SCREENING_DEFAULT_MODE", MODE_OFF)
        or MODE_OFF).strip()
    if admin_default not in SERVER_MODES:
        return MODE_OFF, "DEFAULT_MODE_OFF"
    preference = (getattr(run, "screening_preference", None) or "").strip()
    if preference not in SERVER_MODES:
        preference = admin_default
    if MODE_POWER[preference] > MODE_POWER[admin_default]:
        # Yükseltme YOK: yönetici sınırına kırpılır
        preference = admin_default

    algorithm_version = getattr(run, "algorithm_version", "v2") or "v2"
    if algorithm_version not in SUPPORTED_ALGORITHM_VERSIONS:
        # Candidate contract v2 ve v2_1 icin ayni tarama kararlarini kullanir.
        return MODE_OFF, "ALGORITHM_NOT_SUPPORTED"

    from app.core.policy.channel_strategy import approved_strategy

    if not approved_strategy(workspace):
        return MODE_OFF, "STRATEGY_NOT_APPROVED"

    if preference == MODE_ASSISTIVE and not assistive_allowed(workspace,
                                                              settings):
        return MODE_OFF, "ASSISTIVE_NOT_ENABLED"
    if (
        preference == MODE_ASSISTIVE
        and db is not None
        and candidate_canary_run_count(db, workspace)
        >= CANDIDATE_CANARY_RUNS_PER_WORKSPACE
    ):
        return MODE_OFF, "CANARY_RUN_LIMIT_REACHED"
    return preference, None


__all__ = ["MODE_POWER", "SERVER_MODES", "allowlisted", "assistive_allowed",
           "SUPPORTED_ALGORITHM_VERSIONS",
           "CANDIDATE_CANARY_CONTRACT_VERSION",
           "CANDIDATE_CANARY_MAX_WORKSPACES",
           "CANDIDATE_CANARY_RUNS_PER_WORKSPACE",
           "candidate_canary_run_count", "decide_screening_mode"]
