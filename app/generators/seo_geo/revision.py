# -*- coding: utf-8 -*-
"""SEO içerik revizyonu ("Revize Et"): kapsam alanları, birleştirme ve koruma kapısı.

Kapsam dışındaki HER alan (URL, linkler, görsel alt metinleri, seçilmeyen bölümler)
kaynaktan aynen alınır; koruma prompt'a değil koda dayanır. `verify_preserved`
model çıktısı birleştirildikten sonra VE iddia onarımından sonra çalışır; ihlalde
RevisionScopeError → öneri yazılmaz, revizyon failed olur.
"""
from __future__ import annotations

from typing import Any, Dict, List

SCOPE_FIELDS: Dict[str, tuple] = {
    "title_meta": ("title", "meta_description"),
    "intro": ("intro_paragraph",),
    "body": ("subheadings", "body_sections", "bullet_points", "faq_items"),
}
SCOPES = tuple(SCOPE_FIELDS)
SCOPE_LABELS_TR = {"title_meta": "Başlık ve meta", "intro": "Giriş", "body": "Gövde bölümleri"}
FIELD_LABELS_TR = {
    "title": "Başlık",
    "meta_description": "Meta açıklama",
    "intro_paragraph": "Giriş paragrafı",
    "subheadings": "Alt başlıklar",
    "body_sections": "Gövde bölümleri",
    "bullet_points": "Madde listesi",
    "faq_items": "SSS",
}
# Model yanıtında bulunması ZORUNLU alanlar (opsiyoneller eksikse kaynaktan kalır)
_REQUIRED = {"title_meta": ("title",), "intro": ("intro_paragraph",),
             "body": ("subheadings", "body_sections")}
_LIST_FIELDS = {"subheadings", "body_sections", "bullet_points", "faq_items"}
# Metinden yeniden hesaplanan türev alanlar: koruma karşılaştırmasına girmez
DERIVED_FIELDS = {"word_count", "keyword_count", "keyword_density"}


class RevisionScopeError(ValueError):
    """Kapsam dışı bir alan değişti; öneri kaydedilemez."""


def normalize_scope(scope) -> List[str]:
    picked = {s for s in (scope or []) if s in SCOPE_FIELDS}
    return [s for s in SCOPES if s in picked]


def allowed_fields(scope: List[str]) -> List[str]:
    return [f for s in normalize_scope(scope) for f in SCOPE_FIELDS[s]]


def required_fields(scope: List[str]) -> List[str]:
    return [f for s in normalize_scope(scope) for f in _REQUIRED[s]]


def sanitize_feedback(feedback: str) -> str:
    """Talep veri olarak kalsın: blok ayıracı ve başlık işareti etkisizleştirilir."""
    return feedback.replace("<<<", "‹‹‹").replace(">>>", "›››").replace("#", "＃")


def merge_scoped(source: Dict[str, Any], revised: Dict[str, Any], scope: List[str]) -> Dict[str, Any]:
    """Kaynağın kopyası + yalnız izinli alanların geçerli yeni değerleri.

    Tip uymayan / boş zorunlu alan → ValueError (öneri üretilemedi; kaynak değişmez).
    """
    merged = dict(source)
    for field in allowed_fields(scope):
        if field not in revised:
            if field in required_fields(scope):
                raise ValueError(f"Yapay zekâ yanıtında '{field}' alanı yok.")
            continue
        value = revised[field]
        if field in _LIST_FIELDS:
            if not isinstance(value, list):
                raise ValueError(f"Yapay zekâ yanıtında '{field}' liste değil.")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"Yapay zekâ yanıtında '{field}' boş.")
        merged[field] = value
    if "body" in normalize_scope(scope) and len(merged.get("subheadings") or []) != len(
        merged.get("body_sections") or []
    ):
        raise ValueError("Alt başlık ve gövde bölümü sayıları uyuşmuyor.")
    return merged


def verify_preserved(source: Dict[str, Any], merged: Dict[str, Any], scope: List[str]) -> None:
    """Kayıt kapısı: kapsam dışı (türev olmayan) her alan kaynakla birebir aynı olmalı."""
    allowed = set(allowed_fields(scope))
    keys = (set(source) | set(merged)) - allowed - DERIVED_FIELDS
    changed = sorted(k for k in keys if source.get(k) != merged.get(k))
    if changed:
        labels = ", ".join(FIELD_LABELS_TR.get(k, k) for k in changed)
        raise RevisionScopeError(
            f"Revizyon kaydedilmedi: değiştirilmemesi gereken alanlar değişti ({labels})."
        )


def changed_fields(source: Dict[str, Any], merged: Dict[str, Any], scope: List[str]) -> List[dict]:
    """Önizleme farkı: değişen her izinli alan için {field, label, before, after}."""
    return [
        {"field": f, "label": FIELD_LABELS_TR[f], "before": source.get(f), "after": merged.get(f)}
        for f in allowed_fields(scope)
        if source.get(f) != merged.get(f)
    ]
