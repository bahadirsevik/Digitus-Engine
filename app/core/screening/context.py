# -*- coding: utf-8 -*-
"""Screening bağlamının TEK kanonik üreticisi (plan §3 uygulama kilidi).

Preflight, screening job snapshot'ı, reuse guard'ı, callback/finalize
guard'ı ve `compute_pool_freshness` YALNIZ bu modülün ürettiği payload ve
SHA'yı kullanır. İkinci bir bağlam üreticisi tutulmaz: aynı workspace için
iki farklı hash üretmek reuse'u sessizce bozar ve prompt'u sözleşme dışına
çıkarır.

Fail-closed: onaylı kanal stratejisi yoksa `ScreeningContextMissing`
(çağıran `409 STRATEGY_REQUIRED`'a çevirir) — canlı profile SESSİZCE
düşülmez, boş bağlamla prompt kurulmaz.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Dict, Optional

CONTEXT_CONTRACT_VERSION = "SCRCTX-2026-07-30-v1"
# Prompt'a giren alanlar (sıra sabit; hash bu alan kümesine bağlıdır)
CONTEXT_FIELDS = ("product_definition", "content_strategy", "social_mode",
                  "target_audience")
PLACEHOLDER = "-"


class ScreeningContextMissing(RuntimeError):
    """Onaylı strateji/bağlam yok — screening başlatılamaz (fail-closed)."""


def _normalize_text(value: Optional[str]) -> str:
    """Kanonik metin: NFC + satır sonu birleştirme + kenar boşluğu kırpma.

    İç boşluklar KORUNUR (prompt anlamını değiştirmemek için); yalnız
    görünmez farklar (CRLF, NBSP, unicode form) hash'i oynatmaz.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")
    return text.strip()


def canonical_payload(fields: Dict[str, Any]) -> str:
    """Alan sözlüğü → kanonik JSON (sort_keys, sabit separator, UTF-8)."""
    return json.dumps({k: fields[k] for k in CONTEXT_FIELDS},
                      ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def context_sha256(fields: Dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_payload(fields).encode("utf-8")).hexdigest()


def canonical_screening_context(workspace) -> Dict[str, Any]:
    """Workspace → dondurulabilir screening bağlamı + SHA.

    Dönen `fields` prompt'a AYNEN girer; `context_sha256` kimlik/reuse ve
    freshness karşılaştırmalarının TEK kaynağıdır.
    """
    from app.core.policy.channel_strategy import approved_strategy

    if workspace is None:
        raise ScreeningContextMissing(
            "STRATEGY_REQUIRED: workspace yok — screening bağlamı kurulamaz")
    strategy = approved_strategy(workspace)
    if not strategy:
        raise ScreeningContextMissing(
            "STRATEGY_REQUIRED: onaylı kanal stratejisi yok — screening "
            "bağlamı canlı profilden UYDURULMAZ")
    product_definition = _normalize_text(strategy.get("product_definition"))
    content_strategy = _normalize_text(strategy.get("content_strategy"))
    social_mode = _normalize_text(strategy.get("social_mode"))
    missing = [name for name, value in (
        ("product_definition", product_definition),
        ("content_strategy", content_strategy),
        ("social_mode", social_mode)) if not value]
    if missing:
        raise ScreeningContextMissing(
            f"STRATEGY_REQUIRED: onaylı stratejide zorunlu alan(lar) boş: "
            f"{missing}")
    profile = getattr(workspace, "profile_data", None)
    audience_raw = (profile or {}).get("target_audience") \
        if isinstance(profile, dict) else None
    # Hedef kitle OPSİYONELDİR ama prompt'ta yer tutucu ile temsil edilir;
    # yer tutucu da hash'e girer (sonradan doldurulursa bağlam DEĞİŞİR ve
    # reuse düşer — bilinçli)
    target_audience = _normalize_text(audience_raw) or PLACEHOLDER
    fields = {
        "product_definition": product_definition,
        "content_strategy": content_strategy,
        "social_mode": social_mode,
        "target_audience": target_audience,
    }
    return {
        "contract_version": CONTEXT_CONTRACT_VERSION,
        "fields": fields,
        "canonical_payload": canonical_payload(fields),
        "context_sha256": context_sha256(fields),
        "strategy_version": int(getattr(workspace, "strategy_version", 0) or 0),
        # Codex 5. tur #4: approved_strategy() `approved_fingerprint`
        # döndürür; `fingerprint` yalnız canlı (draft olabilen) yükte var
        "strategy_fingerprint": (strategy.get("approved_fingerprint")
                                 or strategy.get("fingerprint")),
        "strategy_schema_version": strategy.get("schema_version"),
        "target_audience_present": bool(_normalize_text(audience_raw)),
    }


def screening_context_from_snapshot(snapshot: Dict[str, Any]
                                    ) -> Dict[str, Any]:
    """Dondurulmuş job snapshot'ından AYNI hash'i yeniden üretir.

    Worker canlı profili OKUMAZ; job'daki snapshot'ı bu fonksiyondan
    geçirip prompt bağlamını ve doğrulama hash'ini kurar.
    """
    if not isinstance(snapshot, dict):
        raise ScreeningContextMissing("bağlam snapshot'ı dict olmalı")
    fields = {}
    for name in CONTEXT_FIELDS:
        value = _normalize_text(snapshot.get(name))
        if name == "target_audience":
            value = value or PLACEHOLDER
        elif not value:
            raise ScreeningContextMissing(
                f"snapshot'ta zorunlu bağlam alanı boş: {name}")
        fields[name] = value
    return {
        "contract_version": CONTEXT_CONTRACT_VERSION,
        "fields": fields,
        "canonical_payload": canonical_payload(fields),
        "context_sha256": context_sha256(fields),
    }
