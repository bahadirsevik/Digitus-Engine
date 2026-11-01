# -*- coding: utf-8 -*-
"""İddia inceleme kaydı — tek biçim, üç kanal (plan_iddia_denetimi rev.4, N3).

Belirsiz içerik KAYBOLMAZ; işaretlenir. Kayıt:
    {"current": {"evaluated_at": iso, "findings": [...], "applied": [...]},
     "history": [önceki current'lar, en çok 5]}
- findings: hâlâ açık bulgular ({kind, category, span, reason, ...kanal alanları}).
- applied : otomatik düzeltmeler ({..., original, suggestion}) — orijinal metin korunur.
Bu modül bağımlılıksızdır (validators/DB import etmez): bulgular dict ya da
`as_dict()` taşıyan nesne olabilir.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

MAX_HISTORY = 5
MAX_FINDINGS = 60  # tek kayıtta üst sınır (JSON şişmesini önler)
SET_KEY = "claim_review"  # AdGenerationSet.warnings öğesi anahtarı
SOCIAL_RISK_PREFIX = "claim_risk:"
SOCIAL_UNVERIFIED_PREFIX = "claim_unverified:"
SOCIAL_RECORD_PREFIX = "claim_review:"
REASON_RISK_TR = "Açık sonuç/kazanç vaadi — yayın öncesi düzelt"
REASON_UNVERIFIED_TR = "Profilde doğrulanamadı — kontrol gerekli"
KIND_LABEL_TR = {
    "risk": "Risk — yayın öncesi düzelt",
    "unverified": "Profilde doğrulanamadı — kontrol gerekli",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finding_dict(finding: Any, **extra: Any) -> dict:
    base = finding.as_dict() if hasattr(finding, "as_dict") else dict(finding)
    base.update({k: v for k, v in extra.items() if v is not None})
    return base


def make_current(
    findings: Iterable[Any], applied: Iterable[Any] = (), evaluated_at: Optional[str] = None
) -> dict:
    return {
        "evaluated_at": evaluated_at or now_iso(),
        "findings": [finding_dict(f) for f in list(findings)[:MAX_FINDINGS]],
        "applied": [finding_dict(f) for f in list(applied)[:MAX_FINDINGS]],
    }


def push_review(record: Optional[dict], new_current: dict) -> dict:
    """Yeni `current`'ı yazar; öncekini `history`'ye iter (en çok 5, eskiler düşer)."""
    record = record if isinstance(record, dict) else {}
    history = list(record.get("history") or [])
    if record.get("current"):
        history.append(record["current"])
    return {"current": new_current, "history": history[-MAX_HISTORY:]}


def _finding_keys(findings: Optional[Iterable[dict]]) -> list:
    return sorted(
        (str(f.get("kind")), str(f.get("category")), str(f.get("span")),
         str(f.get("group_id")), str(f.get("asset_text")))
        for f in findings or []
    )


def refresh_review(record: Optional[dict], new_current: dict) -> dict:
    """Re-evaluate için: bulgu kümesi (kind+category+span [+grup/asset]) DEĞİŞMEDİYSE yalnız
    `evaluated_at` yenilenir (history'ye boş kayıt itilmez); değiştiyse push_review."""
    cur = (record or {}).get("current")
    if cur and _finding_keys(cur.get("findings")) == _finding_keys(new_current.get("findings")):
        return {**record, "current": {**cur, "evaluated_at": new_current["evaluated_at"]}}
    return push_review(record, new_current)


def open_findings(record: Optional[dict]) -> list:
    return list(((record or {}).get("current") or {}).get("findings") or [])


def kind_counts(findings: Iterable[dict]) -> dict:
    counts = {"risk": 0, "unverified": 0}
    for f in findings:
        if f.get("kind") in counts:
            counts[f["kind"]] += 1
    return counts


def failure_text(findings: Iterable[dict]) -> str:
    """Açık bulguların sınıfa göre okunur metni (SEO details / publish_review)."""
    spans = {"risk": [], "unverified": []}
    for f in findings:
        if f.get("kind") in spans and f.get("span") not in spans[f["kind"]]:
            spans[f["kind"]].append(f.get("span") or "")
    parts = []
    if spans["risk"]:
        parts.append(f"risk: {', '.join(spans['risk'])} ({REASON_RISK_TR})")
    if spans["unverified"]:
        parts.append(f"unverified: {', '.join(spans['unverified'])} ({REASON_UNVERIFIED_TR})")
    return "; ".join(parts)


def readable_failure_text(findings: Iterable[dict]) -> str:
    """publish_review için: 'Risk — yayın öncesi düzelt: a; Profilde doğrulanamadı — …: b'."""
    findings = list(findings)
    parts = []
    for kind in ("risk", "unverified"):
        spans = []
        for f in findings:
            if f.get("kind") == kind and f.get("span") not in spans:
                spans.append(f.get("span") or "")
        if spans:
            parts.append(f"{KIND_LABEL_TR[kind]}: {', '.join(spans)}")
    return "; ".join(parts)


# ── ADS: AdGenerationSet.warnings (liste) içindeki {"claim_review": kayıt} öğesi ──

def set_review(warnings: Any) -> Optional[dict]:
    """warnings listesindeki claim_review kaydı (yoksa None). Sözlük biçimini de tanır."""
    if isinstance(warnings, dict):
        return warnings.get(SET_KEY)
    for w in warnings or []:
        if isinstance(w, dict) and SET_KEY in w:
            return w[SET_KEY]
    return None


def with_set_review(warnings: Any, record: dict) -> list:
    """Diğer uyarıları (atılan negatifler vb.) koruyup claim_review öğesini değiştirir."""
    if isinstance(warnings, dict):
        return [{SET_KEY: record}]
    kept = [w for w in (warnings or []) if not (isinstance(w, dict) and SET_KEY in w)]
    return [*kept, {SET_KEY: record}]


def set_needs_review(warnings: Any) -> bool:
    return bool(open_findings(set_review(warnings)))


def review_summary(warnings: Any) -> dict:
    """AdGenerationSetSummary'ye eklenen additive alanlar."""
    record = set_review(warnings)
    counts = kind_counts(open_findings(record))
    current = (record or {}).get("current") or {}
    return {
        "review_required": bool(counts["risk"] or counts["unverified"]),
        "claim_risk_count": counts["risk"],
        "claim_unverified_count": counts["unverified"],
        # Okuma yüzeyi: güncel bulgular + otomatik düzeltmeler (orijinal/öneri dahil)
        "claim_review": (
            {"findings": list(current.get("findings") or []), "applied": list(current.get("applied") or [])}
            if record else None
        ),
    }


def ads_set_warnings(dropped: Iterable[Any], findings: Iterable[dict], applied: Iterable[dict]) -> list:
    """Set warnings listesi: atılan negatifler + (varsa) claim_review kaydı."""
    warnings = list(dropped or [])
    findings, applied = list(findings or []), list(applied or [])
    if findings or applied:
        warnings.append({SET_KEY: push_review(None, make_current(findings, applied))})
    return warnings


# ── SOSYAL: validation_warnings (list[str]) ──

def social_flag_entries(findings: Iterable[Any]) -> list:
    """`claim_risk:<span>` / `claim_unverified:<span>` öğeleri (sıra ve tekillik korunur)."""
    out = []
    for f in findings:
        d = finding_dict(f)
        prefix = SOCIAL_RISK_PREFIX if d.get("kind") == "risk" else SOCIAL_UNVERIFIED_PREFIX
        entry = prefix + str(d.get("span") or "")
        if entry not in out:
            out.append(entry)
    return out


def social_record_entry(record: dict) -> str:
    return SOCIAL_RECORD_PREFIX + json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def social_record(warnings: Iterable[Any]) -> Optional[dict]:
    for w in warnings or []:
        if isinstance(w, str) and w.startswith(SOCIAL_RECORD_PREFIX):
            try:
                rec = json.loads(w[len(SOCIAL_RECORD_PREFIX):])
            except ValueError:
                return None
            return rec if isinstance(rec, dict) else None
    return None


def social_has_open_flags(warnings: Iterable[Any]) -> bool:
    return any(
        isinstance(w, str) and w.startswith((SOCIAL_RISK_PREFIX, SOCIAL_UNVERIFIED_PREFIX))
        for w in warnings or []
    )


def social_replace_review(
    warnings: Iterable[Any], findings: Iterable[Any], record: Optional[dict]
) -> list:
    """Sosyal claim_* öğelerini yeni bulgu/kayıtla değiştirir; diğer uyarılar korunur."""
    kept = [
        w for w in warnings or []
        if not (isinstance(w, str) and w.startswith(
            (SOCIAL_RISK_PREFIX, SOCIAL_UNVERIFIED_PREFIX, SOCIAL_RECORD_PREFIX)
        ))
    ]
    kept.extend(social_flag_entries(findings))
    if record is not None:
        kept.append(social_record_entry(record))
    return kept


def social_warning_display(warnings: Iterable[Any]) -> list:
    """Export gösterimi: claim_review JSON gövdesi gizlenir, claim_* öğeleri okunur metne çevrilir."""
    out = []
    for w in warnings or []:
        if not isinstance(w, str) or w.startswith(SOCIAL_RECORD_PREFIX):
            continue
        if w.startswith(SOCIAL_RISK_PREFIX):
            w = f"{KIND_LABEL_TR['risk']}: {w[len(SOCIAL_RISK_PREFIX):]}"
        elif w.startswith(SOCIAL_UNVERIFIED_PREFIX):
            w = f"{KIND_LABEL_TR['unverified']}: {w[len(SOCIAL_UNVERIFIED_PREFIX):]}"
        out.append(w)
    return out
