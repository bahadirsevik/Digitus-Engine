# -*- coding: utf-8 -*-
"""v2.1 kanal stratejisi sözleşmesi (plan Faz C).

Üç beyan: product_definition, content_strategy, social_mode (hype|authority).
Tek fingerprint kaynağı: `canonical_channel_strategy_fingerprint` — UI/API,
dispatcher ve manifest AYNI helper'ı kullanır (plan §6).

Kurallar:
- channel_strategy JSON'u her zaman YENİ dict atamasıyla güncellenir
  (in-place mutation SQLAlchemy JSON değişikliğini kaçırır).
- strategy_version yalnız ONAYLI semantik fingerprint gerçekten değişince
  ve invalidate_workspace_outputs ile AYNI transaction'da BİR kez artar.
- Draft düzenleme sürüm/staleness üretmez; taslak asla onay sayılmaz.
- authority modu kaydedilebilir ama DENEYSEL'dir: v2_1 run başlatma kapısı
  (Faz E) authority'de açık hatayla reddeder — sessizce hype koşulmaz.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STRATEGY_SCHEMA_VERSION = 1
SOCIAL_MODES = ("hype", "authority")
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"


class StrategyValidationError(ValueError):
    """Geçersiz strateji girdisi (API 400'e çevirir)."""


def _normalize_text(value: Optional[str]) -> str:
    """Anlamsal metin normalizasyonu: trim + whitespace collapse.

    Türkçe karakter/harf dönüşümü YAPILMAZ — beyan kullanıcı metnidir;
    yalnız boşluk farkları fingerprint'i oynatmasın.
    """
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_strategy_payload(
    product_definition: Optional[str],
    content_strategy: Optional[str],
    social_mode: Optional[str],
) -> Dict[str, Any]:
    mode = (social_mode or "").strip().lower()
    if mode not in SOCIAL_MODES:
        raise StrategyValidationError(
            f"social_mode 'hype' veya 'authority' olmalı (gelen: {social_mode!r})"
        )
    return {
        "product_definition": _normalize_text(product_definition),
        "content_strategy": _normalize_text(content_strategy),
        "social_mode": mode,
        "schema_version": STRATEGY_SCHEMA_VERSION,
    }


def canonical_channel_strategy_fingerprint(strategy: Dict[str, Any]) -> str:
    """TEK fingerprint kaynağı — normalize alanların canonical JSON SHA-256'sı."""
    payload = {
        "product_definition": _normalize_text(strategy.get("product_definition")),
        "content_strategy": _normalize_text(strategy.get("content_strategy")),
        "social_mode": (strategy.get("social_mode") or "").strip().lower(),
        "schema_version": strategy.get("schema_version", STRATEGY_SCHEMA_VERSION),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approved_snapshot_from(previous: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Önceki kayıttan onaylı yükü çıkar (Codex Faz C 2. tur #2 — legacy
    uyumluluk): approved_snapshot varsa o; yoksa ama kayıt status=approved
    (738d858 öncesi biçim) ise üst düzey alanlardan snapshot kurulur.
    Böylece eski-biçim onaylı kayda taslak yazmak onayı YOK ETMEZ."""
    snapshot = previous.get("approved_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("product_definition"):
        return snapshot
    if previous.get("status") == STATUS_APPROVED:
        return {
            "product_definition": previous.get("product_definition"),
            "content_strategy": previous.get("content_strategy"),
            "social_mode": previous.get("social_mode"),
            "schema_version": previous.get(
                "schema_version", STRATEGY_SCHEMA_VERSION
            ),
        }
    return None


def approved_strategy(workspace) -> Optional[Dict[str, Any]]:
    """Onaylı strateji YÜKÜ; yoksa None (draft onay SAYILMAZ).

    Codex Faz C #3: taslak kaydı onaylı snapshot'ı SİLMEZ — üst düzey
    alanlar 'güncel' (draft olabilir) içeriği taşırken, son onaylı yük
    `approved_snapshot` altında korunur. status=draft iken bile mevcut
    onay geçerli kalır (dispatch STRATEGY_REQUIRED'a düşmez).
    """
    strategy = getattr(workspace, "channel_strategy", None)
    if not isinstance(strategy, dict):
        return None
    if strategy.get("status") == STATUS_APPROVED:
        return strategy
    snapshot = strategy.get("approved_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("product_definition"):
        return {
            **snapshot,
            "status": STATUS_APPROVED,
            "approved_at": strategy.get("approved_at"),
            "approved_fingerprint": strategy.get("approved_fingerprint"),
        }
    return None


def strategy_snapshot_for_run(workspace) -> Optional[Dict[str, Any]]:
    """Dispatch-anı manifest snapshot'ı (onaylı strateji + sürüm + fingerprint)."""
    strategy = approved_strategy(workspace)
    if strategy is None:
        return None
    return {
        "product_definition": strategy.get("product_definition"),
        "content_strategy": strategy.get("content_strategy"),
        "social_mode": strategy.get("social_mode"),
        "schema_version": strategy.get("schema_version"),
        "fingerprint": canonical_channel_strategy_fingerprint(strategy),
        "strategy_version": int(getattr(workspace, "strategy_version", 0) or 0),
    }


class StrategySnapshotMissingError(RuntimeError):
    """v2_1: manifest'te strateji snapshot'ı yok/eksik alanlı — 'manifest
    tek kaynaktır' sözleşmesi FAIL-CLOSED (Codex Faz D 3. tur #1). Canlı
    profile düşülmez; AI çağrısı yapılmadan atama durdurulur."""


_SNAPSHOT_REQUIRED_FIELDS = ("product_definition", "content_strategy",
                             "social_mode")


def dispatch_strategy_context(run) -> Optional[Dict[str, Any]]:
    """v2_1 prompt bağlamının TEK kaynağı: dispatch-anı manifest snapshot'ı.

    Codex Faz D #1: canlı BrandProfile OKUNMAZ — atama sürerken strateji
    değişse bile harcanan AI çağrıları ve ara IntentAnalysis kayıtları
    manifest'teki snapshot ile tutarlı kalır (finalize guard'ı yarışı zaten
    düşürür; bu helper ara-durum tutarlılığını sağlar)."""
    manifest = getattr(run, "execution_manifest", None) or {}
    snapshot = manifest.get("strategy_snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def require_dispatch_strategy_context(run) -> Dict[str, Any]:
    """v2_1 için snapshot'ı ZORUNLU kılar (fail-closed, AI çağrısından önce).

    Snapshot yoksa veya zorunlu alanları eksikse StrategySnapshotMissingError
    — canlı profile SESSİZCE düşülmez (Codex Faz D 3. tur #1)."""
    snapshot = dispatch_strategy_context(run)
    if snapshot is None:
        raise StrategySnapshotMissingError(
            "STRATEGY_SNAPSHOT_MISSING: v2_1 koşusunda manifest strateji "
            "snapshot'ı yok — dispatch sözleşmesi bozuk; canlı profile "
            "düşülmez, atama durduruldu."
        )
    missing = [
        field for field in _SNAPSHOT_REQUIRED_FIELDS
        if not str(snapshot.get(field) or "").strip()
    ]
    if missing:
        raise StrategySnapshotMissingError(
            "STRATEGY_SNAPSHOT_MISSING: manifest snapshot'ında zorunlu "
            f"alan(lar) eksik: {missing} — atama durduruldu."
        )
    return snapshot


def draft_strategy_suggestion(workspace) -> Dict[str, Any]:
    """Mevcut müşteri geçişi için taslak ÖNERİSİ (persist ETMEZ, onay değildir).

    product_definition onaylı profilden doldurulur; content_strategy boş
    bırakılır (kullanıcı beyanı şart); social_mode=hype önerilir.
    """
    product_definition = ""
    profile = getattr(workspace, "profile_data", None)
    if isinstance(profile, dict):
        parts = []
        summary = str(profile.get("brand_summary") or "").strip()
        if summary:
            parts.append(summary)
        products = profile.get("products")
        if isinstance(products, list) and products:
            parts.append(
                "Ürünler: " + ", ".join(str(p) for p in products[:5])
            )
        problems = profile.get("problems_solved")
        if isinstance(problems, list) and problems:
            parts.append(
                "Çözülen problemler: " + ", ".join(str(p) for p in problems[:3])
            )
        product_definition = " ".join(parts)
    return {
        "product_definition": _normalize_text(product_definition),
        "content_strategy": "",
        "social_mode": "hype",
        "schema_version": STRATEGY_SCHEMA_VERSION,
        "status": STATUS_DRAFT,
    }


def apply_strategy_update(
    db,
    workspace,
    *,
    product_definition: Optional[str],
    content_strategy: Optional[str],
    social_mode: Optional[str],
    approve: bool,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Strateji yaz (çağıran workspace ROW LOCK'unu almış olmalı).

    Dönüş: {strategy, strategy_version, fingerprint, version_bumped,
    stale_outputs}. Onayda semantik fingerprint değişmediyse sürüm artmaz
    ve invalidation çağrılmaz.
    """
    from app.core.scoring.state_machine import invalidate_workspace_outputs

    normalized = normalize_strategy_payload(
        product_definition, content_strategy, social_mode
    )
    if approve:
        if not normalized["product_definition"]:
            raise StrategyValidationError("Ürün/hizmet tanımı zorunludur")
        if not normalized["content_strategy"]:
            raise StrategyValidationError("İçerik stratejisi beyanı zorunludur")

    previous = getattr(workspace, "channel_strategy", None) or {}
    previous_approved_fp = previous.get("approved_fingerprint")

    new_fp = canonical_channel_strategy_fingerprint(normalized)
    stamp = (now or datetime.now(timezone.utc)).isoformat()

    version_bumped = False
    stale_outputs = 0
    if approve:
        strategy = {
            **normalized,
            "status": STATUS_APPROVED,
            "approved_at": stamp,
            "approved_fingerprint": new_fp,
            # Onaylı yükün ayrı kopyası — sonraki taslak düzenlemeler bunu
            # EZEMEZ (Codex Faz C #3)
            "approved_snapshot": dict(normalized),
        }
        if new_fp != previous_approved_fp:
            workspace.strategy_version = int(workspace.strategy_version or 0) + 1
            # v2 ve v3 stratejiden bağımsızdır (ADR-004: v3 kanal stratejisi kullanmaz).
            # Yalnız legacy v2_1 stratejiyi girdi olarak kullandığı için çıktıları bayatlatılır.
            stale_outputs = invalidate_workspace_outputs(
                db, workspace.id, only_algorithm_version="v2_1"
            )
            version_bumped = True
    else:
        strategy = {
            **normalized,
            "status": STATUS_DRAFT,
            # Son onayın META'sı VE YÜKÜ taşınır — taslak onayı EZEMEZ
            # (Codex Faz C #3): mevcut onaylı snapshot dispatch için geçerli
            # kalır, sürüm/staleness üretilmez
            "approved_at": previous.get("approved_at"),
            "approved_fingerprint": previous_approved_fp,
            "approved_snapshot": _approved_snapshot_from(previous),
        }

    # Her zaman YENİ dict ataması (in-place mutation YOK — plan §6)
    workspace.channel_strategy = strategy
    return {
        "strategy": strategy,
        "strategy_version": int(workspace.strategy_version or 0),
        "fingerprint": new_fp,
        "version_bumped": version_bumped,
        "stale_outputs": stale_outputs,
    }
