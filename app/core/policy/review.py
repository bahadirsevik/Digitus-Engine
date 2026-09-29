"""Rakip/konu incelemesinin paylaşılan çekirdeği + profil yazma kapısı (plan v13).

İki çağıran: onboarding profil onayı (approve_workspace_profile) VE kalıcı
düzenleme endpoint'i (PUT /policy/review). Çağıranlar workspace ROW LOCK almış
olmalıdır; bu modül commit ETMEZ (mutasyonla aynı transaction'da koşar).

Katmanlama: HTTPException FIRLATILMAZ — `PolicyValidationError` üretilir,
router 400'e çevirir.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.policy.competitor_policy import (
    approved_competitor_terms,
    backfill_legacy_source_urls,
    competitor_policy_for,
    normalize_competitor_url,
    reconcile_removed_urls,
    reconcile_url_decision,
)
from app.core.policy.topic_policy import (
    approved_topic_terms,
    merge_normalized,
    parse_excluded_info,
    remove_normalized,
    sync_excluded_terms,
)
from app.core.site_analyzer.turkish_normalizer import normalize_turkish

MAX_COMPETITOR_URLS = 3
MAX_URL_LENGTH = 500
MAX_TERM_LENGTH = 200
DECISION_BLOCK = "block"
DECISION_NOT_COMPETITOR = "not_competitor"


class PolicyValidationError(Exception):
    """Kullanıcı girdisi doğrulama ihlali — router 400'e çevirir."""


def canonical_anchor_fingerprint(anchor_texts: Optional[List[str]]) -> str:
    """Anchor listesinin kanonik hash'i.

    Anchor SINIRLARI ve SIRASI korunur (tek birleşik string DEĞİL — relevance
    her anchor'ı ayrı embed eder; birleşik string sınır kayıplarını gizlerdi).
    Aynı yardımcı hem version karşılaştırmasında hem dispatch manifest'inde
    kullanılır.
    """
    canonical = [
        normalize_turkish(str(a).strip())
        for a in (anchor_texts or [])
        if str(a).strip()
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_profile_data_update(
    db: Session,
    workspace,
    new_profile_data: Dict[str, Any],
    *,
    invalidate_outputs: bool = True,
    manage_policy_version: bool = True,
) -> bool:
    """profile_data için TEK yazma kapısı (v13 invariant #2).

    Router, BackgroundTask ve Celery yolları bu yardımcı dışında
    `workspace.profile_data = ...` YAZMAZ — dağınık noktalardan birinin
    anchor sürümlemesini unutması tüm freshness modelini bozar.

    1) anchor'ları yeniden türetir  2) eski/yeni canonical fingerprint'i
    karşılaştırır  3) fark varsa anchor_version += 1  4) tema listeleri VEYA
    lokasyon filtresi enforcement kimliği değiştiyse (anchor aynı kalsa bile)
    policy_version += 1  5) istenirse çıktıları stale eder  6) JSON kolona
    YENİ dict atar. Commit ETMEZ.

    Lokasyon filtresi bir KABUL politikasıdır (plan_v3_lokasyon_filtresi.md
    §5.2): mod veya muafiyet değişirse mevcut havuz/içerikler enforcement'ı
    artık yansıtmaz ve bayat sayılmalıdır. `enforcement_identity` zaten
    mod-duyarlıdır (`focus_cities` yalnız `focus_only` modunda kimliğe girer,
    muafiyetler kanonikleştirilir) — bu yüzden ham alan karşılaştırması değil
    o kimlik kullanılır.

    `manage_policy_version=False`: çağıran kendi politika snapshot'ını
    karşılaştırıp sürümü kendisi yönetiyorsa (apply_competitor_review) çift
    artış olmasın diye kapatılır.

    Returns: anchor metni gerçekten değişti mi.
    """
    from app.core.policy.location_policy import enforcement_identity
    from app.core.site_analyzer.anchor_builder import build_anchor_texts

    new_profile = dict(new_profile_data or {})
    new_profile["anchor_texts"] = build_anchor_texts(new_profile)

    old_fp = canonical_anchor_fingerprint(
        (workspace.profile_data or {}).get("anchor_texts")
    )
    new_fp = canonical_anchor_fingerprint(new_profile["anchor_texts"])
    old_themes = _profile_theme_sets(workspace)
    old_location = enforcement_identity(workspace.profile_data)

    workspace.profile_data = new_profile  # yeni dict — in-place mutation yasak

    anchor_changed = old_fp != new_fp
    themes_changed = _profile_theme_sets(workspace) != old_themes
    location_changed = enforcement_identity(workspace.profile_data) != old_location

    if anchor_changed:
        workspace.anchor_version = int(workspace.anchor_version or 1) + 1
    if (themes_changed or location_changed) and manage_policy_version:
        # Marka eleme politikası veya lokasyon filtresi değişti: anchor aynı
        # kalsa bile havuzlar bayat
        workspace.policy_version = int(workspace.policy_version or 1) + 1

    if (anchor_changed or themes_changed or location_changed) and invalidate_outputs:
        from app.core.scoring.state_machine import invalidate_workspace_outputs

        invalidate_workspace_outputs(db, workspace.id)
    return anchor_changed


def _validate_competitor_urls(urls: List[str]) -> Dict[str, str]:
    """URL listesi doğrulaması; {canonical_key: original_url} döndürür."""
    if len(urls) > MAX_COMPETITOR_URLS:
        raise PolicyValidationError(
            f"En fazla {MAX_COMPETITOR_URLS} rakip URL'si girilebilir"
        )
    key_map: Dict[str, str] = {}
    for url in urls:
        if len(str(url)) > MAX_URL_LENGTH:
            raise PolicyValidationError("Rakip URL'si çok uzun (max 500 karakter)")
        key = normalize_competitor_url(url)
        if not key:
            raise PolicyValidationError(f"Geçersiz rakip URL: {url}")
        if key in key_map:
            raise PolicyValidationError(
                "Aynı rakip URL birden fazla kez girilemez"
            )
        key_map[key] = url
    return key_map


def _profile_theme_sets(workspace) -> tuple:
    """Marka elemesini yöneten iki tema listesinin kanonik anahtarı.

    Codex #4: `exclude_themes` ve `protected_themes` marka filtresinin
    ENFORCEMENT girdisidir; politika fingerprint'ine girmezlerse tema
    değişip anchor aynı kaldığında (örn. yalnız dışlama metni daraltıldı)
    eleme davranışı değiştiği halde policy_version artmaz ve mevcut
    havuz/içerikler bayat kalmaz.
    """
    profile = getattr(workspace, "profile_data", None) or {}

    def _norm(field: str) -> tuple:
        values = profile.get(field) or []
        if not isinstance(values, list):
            return ()
        return tuple(sorted(
            normalize_turkish(str(v).strip())
            for v in values if str(v).strip()
        ))

    # Ozgulluk politikasi da ENFORCEMENT girdisidir: cekirdek listesi
    # degisirse (bayrak acildiginda) karar davranisi degisir; havuz/icerik
    # BAYAT sayilmali. Ham politika uzerinden anahtarlanir ki gecersiz
    # politikanin duzeltilmesi de bayatlama uretsin.
    from app.core.policy.specificity import policy_identity_key

    return (_norm("exclude_themes"), _norm("protected_themes"),
            policy_identity_key(profile))


def policy_effect_snapshot(workspace) -> tuple:
    """Etkin politika değişti mi tespiti için karşılaştırma anahtarı.

    Kanal allow/block da dahil: conquest'e açmak/kapamak da enforcement'ı
    değiştirir ve mevcut havuz/içerikleri bayatlatır. Profil tema listeleri
    de buraya dahildir (bkz. `_profile_theme_sets`).
    """
    from app.core.policy.location_policy import enforcement_identity

    channel_policy = competitor_policy_for(workspace)
    return (
        tuple(sorted(approved_competitor_terms(workspace))),
        tuple(sorted(approved_topic_terms(workspace))),
        tuple(sorted(channel_policy.items())),
        _profile_theme_sets(workspace),
        # Lokasyon filtresi bir KABUL politikasıdır: mod veya muafiyet
        # değişirse havuz enforcement'ı değişir (plan §5.2). `focus_cities`
        # yalnız `focus_only` modunda kimliğe girer — diğer modlarda
        # saklanan liste değişse bile havuz gereksiz yere bayatlamaz.
        enforcement_identity(getattr(workspace, "profile_data", None)),
    )


_policy_effect_snapshot = policy_effect_snapshot  # geriye dönük iç kullanım


def apply_competitor_review(
    db: Session,
    workspace,
    *,
    competitor_urls: Optional[List[str]],
    decisions: Optional[List[Any]],
    excluded_info: Optional[str],
) -> Dict[str, Any]:
    """Rakip URL + karar + konu dışlama incelemesinin TAMAMI.

    - competitor_urls=None → mevcut liste geçerli kalır.
    - decisions verilmişse TAM REPLACEMENT'tır: normalize edilmiş karar URL
      kümesi geçerli URL kümesine EŞİT olmalı (eksik payload eski kararları
      sessizce koruyamaz). None → kararlara dokunulmaz.
    - excluded_info=None → konu dışlamalarına dokunulmaz.

    Etkin politika VEYA anchor değiştiyse policy_version/anchor_version artar
    ve workspace çıktıları stale edilir. Commit ÇAĞIRANA aittir.
    """
    before = _policy_effect_snapshot(workspace)
    anchor_changed = False

    effective_urls = (
        list(competitor_urls)
        if competitor_urls is not None
        else list(workspace.competitor_urls or [])
    )
    effective_urls = [u for u in (str(x).strip() for x in effective_urls) if u]
    key_map = _validate_competitor_urls(effective_urls)
    valid_keys = set(key_map)

    if decisions is not None:
        seen_keys: set = set()
        parsed_decisions = []
        for decision in decisions:
            url = getattr(decision, "url", None) or (
                decision.get("url") if isinstance(decision, dict) else None
            )
            term = getattr(decision, "term", None)
            if term is None and isinstance(decision, dict):
                term = decision.get("term")
            action = getattr(decision, "decision", None)
            if action is None and isinstance(decision, dict):
                action = decision.get("decision")

            key = normalize_competitor_url(url or "")
            if not key or key not in valid_keys:
                raise PolicyValidationError(f"Geçersiz rakip karar URL'si: {url}")
            if key in seen_keys:
                raise PolicyValidationError(
                    "Aynı rakip URL için birden fazla karar gönderilemez"
                )
            seen_keys.add(key)
            if action not in (DECISION_BLOCK, DECISION_NOT_COMPETITOR):
                raise PolicyValidationError(f"Geçersiz karar: {action}")
            term_text = str(term or "").strip()
            if len(term_text) > MAX_TERM_LENGTH:
                raise PolicyValidationError("Marka adı çok uzun (max 200 karakter)")
            if action == DECISION_BLOCK and not normalize_turkish(term_text):
                # Bozuk istemcinin mevcut bloğu sessizce "serbest bırakma"ya
                # çevirmesini önler (frontend zaten engelliyor — ikinci kat).
                raise PolicyValidationError(
                    "Engelleme kararı için marka adı boş olamaz"
                )
            parsed_decisions.append((key, url, term_text, action))

        # TAM REPLACEMENT: karar kümesi ≡ geçerli URL kümesi
        if seen_keys != valid_keys:
            raise PolicyValidationError(
                "Karar listesi rakip URL listesiyle birebir eşleşmeli "
                "(her URL için tam bir karar gönderin)"
            )

        # Legacy kayıtlar için kontrollü backfill (bir kez review onayında)
        terms = backfill_legacy_source_urls(
            workspace.competitor_terms, effective_urls
        )
        decisions_map: Dict[str, dict] = {}
        for key, url, term_text, action in parsed_decisions:
            approved_term = term_text if action == DECISION_BLOCK else None
            terms = reconcile_url_decision(terms, url, approved_term)
            decisions_map[key] = {
                "url": url,
                "term": term_text,
                "decision": action,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        workspace.competitor_terms = terms  # yeni liste
        existing_decisions = {
            k: v
            for k, v in (workspace.competitor_url_decisions or {}).items()
            if k in valid_keys
        }
        workspace.competitor_url_decisions = {
            **existing_decisions, **decisions_map
        }  # yeni dict

    if competitor_urls is not None:
        workspace.competitor_urls = effective_urls  # yeni liste
        workspace.competitor_terms = reconcile_removed_urls(
            workspace.competitor_terms, effective_urls
        )
        # Listeden çıkan URL'lerin kalıcı kararları da silinir
        workspace.competitor_url_decisions = {
            k: v
            for k, v in (workspace.competitor_url_decisions or {}).items()
            if k in valid_keys
        }

    if excluded_info is not None:
        new_value = str(excluded_info).strip() or None
        # Provenance'lı yeniden birleşim (Codex v9 #5): eski kullanıcı
        # terimleri exclude_themes'ten çıkarılır — silinen terim listede
        # sıkışıp kalmaz. ÜRÜN KARARI: kullanıcı kaldırması aynı isimli AI
        # dışlamasını da kaldırır (kullanıcı üstün; ayrı ai_exclude_themes
        # provenance kolonu bilinçli yok).
        old_user_terms = parse_excluded_info(workspace.excluded_info)
        profile = dict(workspace.profile_data or {})
        ai_only = remove_normalized(profile.get("exclude_themes"), old_user_terms)
        workspace.excluded_info = new_value
        new_user_terms = parse_excluded_info(new_value)
        profile["exclude_themes"] = merge_normalized(new_user_terms, ai_only)
        # policy_version'ı AŞAĞIDAKİ snapshot karşılaştırması yönetir
        # (tema listeleri artık o snapshot'ın parçası) — çift artış olmasın
        anchor_changed = apply_profile_data_update(
            db, workspace, profile,
            invalidate_outputs=False,
            manage_policy_version=False,
        )

        topic = dict(workspace.topic_policy or {})
        topic["excluded_terms"] = sync_excluded_terms(
            topic.get("excluded_terms"), new_user_terms
        )
        topic.setdefault("excluded_aliases", [])
        workspace.topic_policy = topic  # yeni dict

    policy_changed = _policy_effect_snapshot(workspace) != before
    if policy_changed:
        workspace.policy_version = int(workspace.policy_version or 1) + 1

    stale_count = 0
    if policy_changed or anchor_changed:
        from app.core.scoring.state_machine import invalidate_workspace_outputs

        stale_count = invalidate_workspace_outputs(db, workspace.id)

    return {
        "policy_changed": policy_changed,
        "anchor_changed": anchor_changed,
        "stale_outputs": stale_count,
        "policy_version": workspace.policy_version,
        "anchor_version": workspace.anchor_version,
    }
