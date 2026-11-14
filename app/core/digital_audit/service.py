"""Dijital denetim yasam dongusu: baslatma (tek aktif), calistirma, bayat uzlastirma, soru formu.

Durumlar: pending -> running -> completed | partial | failed.
Tum durum gecisleri KOSULLU UPDATE ile yazilir: suresi dolup uzlastirilan bir denetime
gec kalan worker yazamaz.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy import exists, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.digital_audit import runner
from app.core.digital_audit.signals import site_key
from app.database.models import BrandProfile, DigitalAudit, DigitalAuditTarget

ACTIVE = ("pending", "running")
MAX_COMPETITORS = 3
# Bir hedef en fazla ~240 sn tarama + 2 hiz olcumu surer; kira her hedeften sonra yenilenir.
LEASE_SECONDS = 600
STALE_MESSAGE = "Denetim zamanında tamamlanmadı (sunucu yanıt vermeyi bıraktı). O ana kadar toplanan sonuçlar korundu."
TARGET_DONE = ("completed", "blocked")

# Disaridan gorulemeyen maddeler: musteriye sorulur. Otomatik bulgulardan ayri saklanir.
_YES_NO = ["Evet", "Hayır", "Bilinmiyor"]
QUESTIONS: List[Dict] = [
    {"key": "search_console", "label": "Google Search Console bağlı mı?", "options": _YES_NO},
    {"key": "conversion_tracking", "label": "Dönüşüm takibi (form, arama, satış) tanımlı mı?", "options": _YES_NO},
    {"key": "remarketing", "label": "Remarketing yapılıyor mu?", "options": _YES_NO},
    {"key": "crm", "label": "CRM ya da talep takip yöntemi var mı?", "options": _YES_NO},
    {"key": "newsletter", "label": "E-bülten / e-posta listesi kullanılıyor mu?", "options": _YES_NO},
    {"key": "whatsapp_business", "label": "WhatsApp hattı Business hesabı mı?", "options": _YES_NO},
    {"key": "domain_owner", "label": "Alan adı ve hosting hesabı kimde?", "options": None},
    {"key": "gbp_claimed", "label": "Google İşletme Profili sahiplenilmiş mi?", "options": _YES_NO},
]
_QUESTION_KEYS = {q["key"] for q in QUESTIONS}


class AuditInputError(ValueError):
    """Gecersiz baslatma girdisi (URL/rakip sayisi)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_url(raw: str) -> str:
    url = runner.normalize_url(raw)
    parsed = urlparse(url)
    if not parsed.hostname or "." not in parsed.hostname or len(url) > 500:
        raise AuditInputError(f"Geçersiz site adresi: {raw!r}")
    return url


def reconcile_expired(db: Session, brand_profile_id: int) -> int:
    """Kirasi dolmus aktif denetimleri kapatir (okuma/baslatma ani). Commit EDER.

    Sonuc kaydetmis en az bir hedef varsa 'partial' (toplananlar korunur), yoksa 'failed'.
    """
    expired = (
        DigitalAudit.brand_profile_id == brand_profile_id,
        DigitalAudit.status.in_(ACTIVE),
        DigitalAudit.lease_expires_at < _now(),
    )
    has_results = exists().where(
        DigitalAuditTarget.audit_id == DigitalAudit.id,
        DigitalAuditTarget.status.in_(TARGET_DONE),
    )
    values = dict(error_message=STALE_MESSAGE, completed_at=_now(), lease_expires_at=None)
    changed = 0
    for condition, status in ((has_results, "partial"), (~has_results, "failed")):
        result = db.execute(
            update(DigitalAudit).where(*expired, condition)
            .values(status=status, **values).execution_options(synchronize_session=False)
        )
        changed += result.rowcount or 0
    db.commit()
    if changed:
        db.expire_all()
        logger.warning(f"Bayat dijital denetim kapatıldı: {changed} kayıt (workspace={brand_profile_id})")
    return changed


def _active(db: Session, brand_profile_id: int) -> Optional[DigitalAudit]:
    return (
        db.query(DigitalAudit)
        .filter(DigitalAudit.brand_profile_id == brand_profile_id, DigitalAudit.status.in_(ACTIVE))
        .first()
    )


def start_audit(db: Session, workspace: BrandProfile, own_url: Optional[str],
                competitor_urls: List[str]) -> Tuple[DigitalAudit, bool]:
    """Yeni denetim kaydi acar ya da suren denetimi dondurur: (audit, reused).

    Workspace basina tek aktif denetim DB'de garanti edilir (uq_digital_audit_active);
    es zamanli iki istekten biri IntegrityError alir ve digerinin kaydini dondurur.
    """
    reconcile_expired(db, workspace.id)
    active = _active(db, workspace.id)
    if active:
        return active, True

    own = _clean_url(own_url or workspace.company_url or "")
    competitors: List[str] = []
    for raw in competitor_urls or []:
        if not (raw or "").strip():
            continue
        url = _clean_url(raw)
        key = site_key(urlparse(url).hostname)
        if key == site_key(urlparse(own).hostname) or key in {site_key(urlparse(c).hostname) for c in competitors}:
            continue
        competitors.append(url)
    if len(competitors) > MAX_COMPETITORS:
        raise AuditInputError(f"En fazla {MAX_COMPETITORS} rakip eklenebilir.")

    audit = DigitalAudit(
        brand_profile_id=workspace.id, status="pending", progress=0,
        task_id=str(uuid.uuid4()), lease_expires_at=_now() + timedelta(seconds=LEASE_SECONDS),
        scope={"method_version": runner.METHOD_VERSION,
               "limits": {"max_pages": runner.MAX_PAGES, "max_link_checks": runner.MAX_LINK_CHECKS}},
    )
    audit.targets = [DigitalAuditTarget(position=0, kind="own", url=own)] + [
        DigitalAuditTarget(position=i + 1, kind="competitor", url=url) for i, url in enumerate(competitors)
    ]
    db.add(audit)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = _active(db, workspace.id)
        if active:
            return active, True
        raise
    db.refresh(audit)
    return audit, False


def mark_dispatch_failed(db: Session, audit: DigitalAudit) -> None:
    audit.status = "failed"
    audit.error_message = "Denetim kuyruğa alınamadı. Lütfen tekrar deneyin."
    audit.completed_at = _now()
    audit.lease_expires_at = None
    db.commit()


def _still_ours(db: Session, audit_id: int, task_id: str, **values) -> bool:
    """Denetim hala bu worker'da mi? Oyleyse kirayi yeniler (+values). Commit ETMEZ."""
    result = db.execute(
        update(DigitalAudit)
        .where(DigitalAudit.id == audit_id, DigitalAudit.status == "running", DigitalAudit.task_id == task_id)
        .values(lease_expires_at=_now() + timedelta(seconds=LEASE_SECONDS), **values)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def run_audit(db: Session, audit_id: int, task_id: str) -> str:
    """Denetimi calistirir; her hedefin sonucu bitince KAYDEDILIR (kismi sonuc korunur).

    Donus: completed | partial | failed | skipped (zaten alinmis/bitmis) | superseded (kira kaybedildi).
    """
    from celery.exceptions import SoftTimeLimitExceeded

    claimed = db.execute(
        update(DigitalAudit)
        .where(DigitalAudit.id == audit_id, DigitalAudit.status == "pending", DigitalAudit.task_id == task_id)
        .values(status="running", started_at=_now(), lease_expires_at=_now() + timedelta(seconds=LEASE_SECONDS))
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if not claimed:
        return "skipped"  # cift teslim ya da bu arada kapatilmis denetim

    targets = (
        db.query(DigitalAuditTarget).filter(DigitalAuditTarget.audit_id == audit_id)
        .order_by(DigitalAuditTarget.position).all()
    )
    for index, target in enumerate(targets):
        url, own = target.url, target.kind == "own"
        try:
            result = runner.run_target(url, own)
        except SoftTimeLimitExceeded:
            logger.warning(f"Dijital denetim süre sınırına takıldı (audit={audit_id})")
            break
        except Exception:
            # Tek hedefin beklenmeyen hatasi digerlerini durdurmaz.
            logger.exception(f"Dijital denetim hedefi başarısız (audit={audit_id}, url={url})")
            result = {"status": "failed", "pages_checked": 0, "findings": [],
                      "error_message": "Bu site denetlenirken beklenmeyen bir hata oluştu."}
        if not _still_ours(db, audit_id, task_id, progress=int((index + 1) / len(targets) * 95)):
            db.rollback()
            return "superseded"
        target.status = result["status"]
        target.pages_checked = result["pages_checked"]
        target.findings = result["findings"]
        target.error_message = result.get("error_message")
        target.completed_at = _now()
        db.commit()

    statuses = [t.status for t in targets]
    if all(s in TARGET_DONE for s in statuses):
        final = "completed"
    elif any(s in TARGET_DONE for s in statuses):
        final = "partial"
    else:
        final = "failed"
    error = None if final == "completed" else (
        "Bazı siteler denetlenemedi; diğerlerinin sonuçları aşağıda." if final == "partial"
        else "Hiçbir site denetlenemedi.")
    written = db.execute(
        update(DigitalAudit)
        .where(DigitalAudit.id == audit_id, DigitalAudit.status == "running", DigitalAudit.task_id == task_id)
        .values(status=final, progress=100, completed_at=_now(), lease_expires_at=None, error_message=error)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return final if written else "superseded"


def save_answers(db: Session, audit: DigitalAudit, answers: Dict[str, Dict]) -> None:
    """Soru formu cevaplari. Otomatik bulgulara DOKUNMAZ; ayri alanda saklanir."""
    unknown = set(answers) - _QUESTION_KEYS
    if unknown:
        raise AuditInputError(f"Bilinmeyen soru: {sorted(unknown)[0]}")
    cleaned = {}
    for key, item in answers.items():
        value = ((item or {}).get("value") or "").strip()[:300]
        note = ((item or {}).get("note") or "").strip()[:1000]
        if value or note:
            cleaned[key] = {"value": value or None, "note": note or None}
    audit.answers = cleaned
    db.commit()


def serialize(audit: DigitalAudit, detail: bool = False) -> Dict:
    data = {
        "id": audit.id,
        "status": audit.status,
        "progress": audit.progress,
        "error_message": audit.error_message,
        "created_at": audit.created_at,
        "started_at": audit.started_at,
        "completed_at": audit.completed_at,
        "targets": [
            {"id": t.id, "kind": t.kind, "url": t.url, "status": t.status,
             "pages_checked": t.pages_checked, "error_message": t.error_message,
             **({"findings": t.findings or []} if detail else {})}
            for t in audit.targets
        ],
    }
    if detail:
        data["scope"] = audit.scope or {}
        data["answers"] = audit.answers or {}
        data["questions"] = QUESTIONS
    return data
