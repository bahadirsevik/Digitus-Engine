"""Motor v3 asama rotasi — (provider, model) cozumu (plan_claude_gemini §4 Motor).

SAF fonksiyon: DB'ye ve AI'ya dokunmaz. YALNIZ orkestratorun muhurleme
ANINDA cagrilir; muhurden SONRA her okuma `persistence.stage_route` ile
manifest snapshot'indan yapilir (koşu ortasinda route degisikligi koşuyu
etkilemez). `RoutedAIService._resolve` ile AYNI dogrulama; model adindan
provider TAHMIN EDILMEZ.
"""
from __future__ import annotations

from typing import Tuple

from app.config import settings
from app.generators.ai_service import VALID_PROVIDERS, AIResponseError

DEFAULT_PROVIDER = "gemini"


def resolve_stage_route(stage: str, default_model: str) -> Tuple[str, str]:
    """Route yoksa ("gemini", default_model); varsa dogrulanmis (provider, model)."""
    routes = settings.AI_STAGE_ROUTES or {}
    if not routes:
        return DEFAULT_PROVIDER, default_model
    from app.core.constants import AI_STAGES

    unknown = [key for key in routes if key not in AI_STAGES]
    if unknown:
        raise AIResponseError(
            f"AI_STAGE_ROUTES bilinmeyen stage anahtarlari: {unknown} "
            "— yazim hatasi olabilir (fail-closed)")
    if stage not in routes:
        return DEFAULT_PROVIDER, default_model
    route = routes[stage]
    if not isinstance(route, dict) or not route:
        raise AIResponseError(
            f"AI_STAGE_ROUTES[{stage}] bos/gecersiz: {route!r} — "
            "provider+model zorunlu (fail-closed)")
    provider = route.get("provider")
    model = route.get("model")
    if provider not in VALID_PROVIDERS:
        raise AIResponseError(
            f"AI_STAGE_ROUTES[{stage}].provider gecersiz: {provider!r}")
    if not isinstance(model, str) or not model.strip():
        raise AIResponseError(f"AI_STAGE_ROUTES[{stage}].model zorunlu")
    return provider, model
