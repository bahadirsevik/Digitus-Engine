"""Relevance anchor üretimi + exclude_themes çelişki temizliği.

Sorun (plan5 Kök Neden 3): `anchor_texts` eski/dışlanan temaları içerince
(örn. "temettü", "grafik") relevance embedding'i tam da kullanıcının DIŞLADIĞI
temaları yüksek puanlıyor. Bu modül, anchor'ları exclude_themes ile örtüşen
profil kalemlerini AYIKLAYARAK üretir.

Tema eşleşme kuralı `theme_matcher` modülünde durur (import kapısıyla ortak).
Kimlik korunan re-export'lar mevcut çağıranlar için tutulur.

Faz D (firma profili düzeltmesi): `services` ve `protected_themes` de anchor
üretir. `services` daha önce hiç kullanılmıyordu — Gemini marka filtresi bu
alanı görürken relevance görmüyordu (özellikle Digitus'un SEO/GEO/Ads/analytics
hizmetleri eksik temsil ediliyordu).

NOT: Tam semantik değildir; codex'in dediği gibi "pratik bir azaltma".
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.core.site_analyzer.theme_matcher import (  # noqa: F401  (re-export)
    filter_items_by_exclude,
    item_is_excluded,
    item_matches_any_theme,
    _theme_stem_sets,
)
from app.core.site_analyzer.turkish_normalizer import normalize_turkish

# UI'da gösterilen grup etiketleri ("Marka odakları" kaynak kartları)
# DİKKAT: "products" etiketi eskiden "Ürün ve hizmetler"di ama `services`
# anchor'a HİÇ girmiyordu (plan Faz D bulgusu) — etiket kullanıcıya yanlış
# bilgi veriyordu. Artık iki ayrı grup var.
_GROUP_LABELS = {
    "products": "Ürünler",
    "services": "Hizmetler",
    "use_cases": "Kullanım alanları",
    "problems_solved": "Çözülen problemler",
    "protected_themes": "Korunacak temalar",
    "brand_terms": "Marka terimleri",
    "sector_audience": "Sektör ve hedef kitle",
}

# Deterministik anchor sırası (plan Faz D). Sıra fingerprint'e girer
# (`canonical_anchor_fingerprint`) — değiştirmek anchor_version üretir.
_LIST_GROUP_ORDER = (
    "products",
    "services",
    "use_cases",
    "problems_solved",
)


def _dedupe(items: List[str]) -> List[str]:
    """Normalize edilmiş metne göre tekilleştirir; ilk görülen sıra korunur.

    Görünmez boşluk/büyük-küçük harf/Türkçe karakter farkı gereksiz anchor
    sürümü üretmesin diye normalize edilmiş anahtar kullanılır; listede ise
    kullanıcının yazdığı ORİJİNAL metin kalır.
    """
    seen = set()
    out: List[str] = []
    for item in items:
        key = normalize_turkish(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_anchor_groups(profile_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Anchor'ları kaynak profil alanına göre gruplu üretir.

    Her grup: {source_field, label, kept_items, excluded_items, anchor}.
    `build_anchor_texts` çıktısı == [g["anchor"] for g in groups] (sıra dahil).
    Boş kalan gruplar listeye girmez (anchor listesiyle birebir eşleşme için).
    """
    pd = profile_data if isinstance(profile_data, dict) else {}
    exclude = pd.get("exclude_themes")
    protected = [
        t for t in (pd.get("protected_themes") or [])
        if isinstance(t, str) and t.strip()
    ]
    theme_sets = _theme_stem_sets(exclude)
    groups: List[Dict[str, Any]] = []

    def _add_list_group(field: str) -> None:
        raw = _dedupe(
            [i for i in (pd.get(field) or []) if isinstance(i, str) and i.strip()]
        )
        # Koruma üstündür: açıkça korunan temaya oturan kalem, geniş bir
        # dışlama temasıyla örtüşse bile anchor'da kalır (plan Faz D).
        kept = filter_items_by_exclude(raw, exclude, protected)
        excluded = [i for i in raw if i not in kept] if theme_sets else []
        if kept:
            groups.append({
                "source_field": field,
                "label": _GROUP_LABELS[field],
                "kept_items": kept,
                "excluded_items": excluded,
                "anchor": " ".join(kept),
            })

    for field in _LIST_GROUP_ORDER:
        _add_list_group(field)

    # Korunacak temalar: exclude filtresinden GEÇMEZ. Bu grubun tamamı zaten
    # kullanıcının "bu konu kapsam içidir" beyanıdır; geniş bir dışlamanın onu
    # ayıklaması tam olarak düzeltmeye çalıştığımız hatadır.
    protected_items = _dedupe(protected)
    if protected_items:
        groups.append({
            "source_field": "protected_themes",
            "label": _GROUP_LABELS["protected_themes"],
            "kept_items": protected_items,
            "excluded_items": [],
            "anchor": " ".join(protected_items),
        })

    brand_terms = _dedupe(
        [b for b in (pd.get("brand_terms") or []) if isinstance(b, str) and b.strip()]
    )
    if brand_terms:
        groups.append({
            "source_field": "brand_terms",
            "label": _GROUP_LABELS["brand_terms"],
            "kept_items": brand_terms,
            "excluded_items": [],
            "anchor": " ".join(brand_terms),
        })

    sector = pd.get("sector", "") or ""
    audience = pd.get("target_audience", "") or ""
    combined = f"{sector} {audience}".strip()
    if combined:
        groups.append({
            "source_field": "sector_audience",
            "label": _GROUP_LABELS["sector_audience"],
            "kept_items": [s for s in (sector, audience) if s],
            "excluded_items": [],
            "anchor": combined,
        })

    return groups


def build_anchor_texts(profile_data: Dict[str, Any]) -> List[str]:
    """Profil alanlarından relevance anchor'ları üretir; exclude_themes ayıklanır.

    `_generate_anchors` (yeni profiller / C1) ve confirm akışı (mevcut
    profiller / B4b) aynı bu yardımcıyı kullanmalı.
    """
    return [group["anchor"] for group in build_anchor_groups(profile_data)]
