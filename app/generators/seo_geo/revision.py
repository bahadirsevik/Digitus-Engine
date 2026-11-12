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


# ── Kayıtlı değerlendirme bağlamı + bozulan kontroller ─────────────────────────
# Prompt'a YALNIZ kayıtlı sonuçlar gider (SEO checks_json, GEO kriter bayrakları ve
# değerlendiricinin improvement_notes'u). Gerekçesi kayıtta olmayan kritere gerekçe
# yazılmaz; yalnız kriter adı geçer. Öncelik: kullanıcı talebi > kapsam > geçen kontroller.

_STATUS_RANK = {"pass": 2, "partial": 1, "fail": 0}


def evaluation_items(seo: Any, geo: Any) -> List[Dict[str, Any]]:
    """Satır ya da denetleyici sonuç sözlüğü → madde bazlı durum listesi (tek biçim)."""
    from types import SimpleNamespace

    from app.compliance.publish_review import checklist_items_from_rows

    if isinstance(seo, dict):
        seo = SimpleNamespace(checks_json=seo.get("checks") or [])
    if isinstance(geo, dict):
        geo = SimpleNamespace(**geo)
    return checklist_items_from_rows(seo, geo)


def evaluation_block(seo_row: Any, geo_row: Any) -> str:
    from app.compliance.publish_review import geo_evaluation_source

    items = evaluation_items(seo_row, geo_row)
    failing = [i for i in items if i["status"] in ("fail", "partial")]
    passing = [i for i in items if i["channel"] == "SEO" and i["status"] == "pass"]
    notes = (geo_row.improvement_notes or "").strip() if geo_evaluation_source(geo_row) == "ai" else ""
    if not failing and not notes and not passing:
        return "— (kayıtlı değerlendirme yok)"
    lines = []
    if failing:
        lines.append("Karşılanmayan kriterler:")
        lines += [f"- {i['label']}" + (f" — {i['detail']}" if i["detail"] else "") for i in failing]
    if notes:
        # Not model çıktısıdır: talimat değil, veri olarak işaretlenir
        lines += ["Değerlendirici notu (GEO, yalnız bilgi):", "<<<NOT", sanitize_feedback(notes), "NOT>>>"]
    if passing:
        lines.append("Şu an geçen SEO kontrolleri:")
        lines += [f"- {i['label']}" for i in passing]
    return "\n".join(lines)


def check_regressions(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Önce geçen (ya da kısmi) olup sonra kötüleşen kontroller. Değerlendirilmeyen atlanır."""
    prev = {(i["channel"], i["criterion"]): i for i in before}
    out = []
    for a in after:
        b = prev.get((a["channel"], a["criterion"]))
        if b is None or b["status"] not in _STATUS_RANK or a["status"] not in _STATUS_RANK:
            continue
        if _STATUS_RANK[a["status"]] < _STATUS_RANK[b["status"]]:
            out.append({
                "criterion": a["criterion"], "label": a["label"], "channel": a["channel"],
                "before": b["status_label"], "after": a["status_label"],
                "before_detail": b["detail"], "after_detail": a["detail"],
            })
    return out
