"""Deneme koşusu ÇALIŞTIRMA SNAPSHOT'ı (DENETİM ve TEKRARLANABİLİRLİK).

Bir deneme koşusu başlatıldığında, koşunun hangi bağlamda çalıştığı
denetlenebilir ve tekrarlanabilir olmalıdır: hangi kelime evreni, hangi
profil/anchor/politika/strateji sürümü, hangi provider/model/route, hangi
screening modu ve hangi maliyet tavanı.

Bu snapshot koşu oluşturma anında (workspace satırı KİLİTLİYKEN) hesaplanır
ve run manifest'ine + yetki denetim kaydına yazılır.

KAPSAM SINIRI: snapshot bir KAPI DEĞİLDİR. Önceden onaylanmış bir kapasite
veya payload ile EŞLEŞTİRME amacıyla KULLANILMAZ; kapasiteleri kullanıcı
UI'dan serbestçe seçer. Ücretli koşu sınırları `trial_authorization`
modülündedir (izinli kombinasyon, başarılı koşu kotası, delinemez toplam
maliyet sınırı).

Kelime kimliği (keyword_id, canonical metin) çiftleri üzerinden hesaplanır:
satır SAYISI ayrıca raporlanır ama kimlik SAYIDAN türetilmez.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

SNAPSHOT_CONTRACT_VERSION = "TRIAL-EXECUTION-SNAPSHOT-2026-08-05-v1"
SNAPSHOT_KEY = "execution_snapshot"
SNAPSHOT_SHA_KEY = "execution_snapshot_sha256"


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def keyword_identity(db, workspace_id: int) -> Dict[str, Any]:
    """(keyword_id, canonical metin) çiftlerinin kanonik imzası.

    Satır SAYISI ayrıca raporlanır ama kimlik SAYIDAN türetilmez: aynı
    sayıda satırla farklı kelime kümesi FARKLI bir koşudur.
    """
    import sqlalchemy as sa

    from app.core.site_analyzer.turkish_normalizer import normalize_turkish

    rows = db.execute(sa.text("""
        SELECT w.keyword_id, k.keyword
        FROM workspace_keywords w JOIN keywords k ON k.id = w.keyword_id
        WHERE w.brand_profile_id = :w ORDER BY w.keyword_id
    """), {"w": workspace_id}).all()
    pairs: List[List[Any]] = [[int(keyword_id), normalize_turkish(text or "")]
                              for keyword_id, text in rows]
    return {"sha256": _sha(pairs), "row_count": len(pairs)}


def route_snapshot(settings=None) -> Dict[str, Any]:
    """Aşama bazında ETKİN provider/model (route tablosu + legacy zincir)."""
    from app.config import settings as default_settings
    from app.core.constants import AI_STAGES
    from app.generators.ai_service import RoutedAIService

    settings = settings or default_settings
    resolver = RoutedAIService.__new__(RoutedAIService)  # sadece _resolve
    stages: Dict[str, Dict[str, Optional[str]]] = {}
    for stage in AI_STAGES:
        provider, model = resolver._resolve(stage, None, None)
        if not model:
            # Legacy yol: model backend zincirinde çözülür
            # (GeminiService._execute: AI_STAGE_MODELS[stage] or model_name)
            model = ((settings.AI_STAGE_MODELS or {}).get(stage)
                     or settings.GEMINI_MODEL)
        stages[stage] = {"provider": provider, "model": model}
    return {
        "stages": stages,
        "default_model": settings.GEMINI_MODEL,
        "thinking_level": getattr(settings, "GEMINI_THINKING_LEVEL", None),
        "stage_models": dict(settings.AI_STAGE_MODELS or {}),
        "stage_routes": dict(getattr(settings, "AI_STAGE_ROUTES", {}) or {}),
    }


def screening_snapshot(db, workspace, run, settings=None) -> Dict[str, Any]:
    from app.config import settings as default_settings
    from app.core.screening.auto_trigger import decide_screening_mode

    settings = settings or default_settings
    mode, reason = decide_screening_mode(run, workspace, settings=settings,
                                         db=db)
    return {
        "enabled": bool(getattr(settings, "ENABLE_CORPUS_SCREENING", False)),
        "default_mode": str(getattr(settings,
                                    "CORPUS_SCREENING_DEFAULT_MODE", "off")),
        "decided_mode": mode,
        "skip_reason": reason,
        "allowlist": sorted(int(x) for x in
                            (getattr(settings,
                                     "CORPUS_SCREENING_ALLOWLIST", None) or [])),
        "technical_ui": bool(getattr(settings,
                                     "CORPUS_SCREENING_TECHNICAL_UI", False)),
        "provider": str(getattr(settings, "CORPUS_SCREENING_PROVIDER", "")),
        "model": str(getattr(settings, "CORPUS_SCREENING_MODEL", "")),
        "max_keywords": int(getattr(settings,
                                    "CORPUS_SCREENING_MAX_KEYWORDS", 0) or 0),
    }


def cost_cap_snapshot(settings=None) -> Dict[str, Any]:
    """Koşu açılışında yürürlükte olan yönetici maliyet tavanları.

    DENETİM kaydıdır. Workspace başına DELİNEMEZ toplam sınır ayrıdır ve
    yetki kaydında yaşar (`trial_authorization.assignment_cost_cap_usd`);
    sağlayıcı çağrısından önce her kapıda ölçülür.
    """
    from app.config import settings as default_settings

    settings = settings or default_settings
    screening = float(getattr(settings,
                              "CORPUS_SCREENING_MAX_APPROVED_USD", 0) or 0)
    downstream = float(getattr(settings,
                               "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 0) or 0)
    return {
        "screening_max_approved_usd": round(screening, 6),
        "downstream_max_approved_usd": round(downstream, 6),
        "combined_max_approved_usd": round(screening + downstream, 6),
        "rule": ("Denetim kaydı: koşu açılışında yürürlükteki yönetici "
                 "tavanları. Deneme workspace'inin delinemez toplam sınırı "
                 "yetki kaydındadır (assignment_cost_cap_usd)."),
    }


def build_execution_snapshot(db, workspace, run, *, settings=None
                             ) -> Dict[str, Any]:
    """Çalıştırma bağlamının kanonik mührü (workspace KİLİTLİYKEN çağrılır)."""
    from app.core.policy.review import canonical_anchor_fingerprint
    from app.core.policy.specificity import policy_fingerprint

    profile = workspace.profile_data or {}
    strategy = ((workspace.channel_strategy or {}).get("approved_snapshot")
                or {})
    snapshot = {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "workspace_id": int(workspace.id),
        "keyword_identity": keyword_identity(db, workspace.id),
        "profile_data_sha256": _sha(profile),
        "anchor": {
            "version": workspace.anchor_version,
            "fingerprint": canonical_anchor_fingerprint(
                profile.get("anchor_texts")),
        },
        "policy": {
            "version": workspace.policy_version,
            "fingerprint": policy_fingerprint(profile),
        },
        "strategy": {
            "version": workspace.strategy_version,
            "fingerprint": (workspace.channel_strategy or {}).get(
                "approved_fingerprint"),
            "social_mode": strategy.get("social_mode"),
        },
        "route": route_snapshot(settings),
        "screening": screening_snapshot(db, workspace, run, settings),
        "cost_cap": cost_cap_snapshot(settings),
    }
    return snapshot


def snapshot_sha256(snapshot: Dict[str, Any]) -> str:
    return _sha(snapshot)


__all__ = ["SNAPSHOT_CONTRACT_VERSION", "SNAPSHOT_KEY", "SNAPSHOT_SHA_KEY",
           "build_execution_snapshot", "cost_cap_snapshot",
           "keyword_identity", "route_snapshot", "screening_snapshot",
           "snapshot_sha256"]
