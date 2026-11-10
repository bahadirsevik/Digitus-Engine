"""Profil analizi "attempt" token'i — eski koşunun yeni sonucu ezmesini engeller.

Sorun (plan_yapilacaklar.md 2.2, ADR-002 kalan riski): site profil analizi
FastAPI BackgroundTasks ile web process içinde koşar. 15 dakikayı aşan CANLI
bir analiz okuma anında `failed` görünür (stuck_janitor); kullanıcı tekrar
başlatırsa iki koşu paralel yürür ve eski koşunun bitişteki koşulsuz
yazımları (başarı VE hata) yeni koşunun sonucunu ya da onaylanmış profili ezer.

Çözüm: `brand_profiles.analysis_attempt_id`.
- Her başlatma satır kilidi altında yeni bir uuid4 yazar (`start_attempt`) ve
  aynı değeri task'a geçirir.
- Task'ın BÜTÜN yazımları (`running` geçişi, başarı yazımı, her `failed`
  yazımı) token'a koşulludur: satır `with_for_update` ile (taze değerlerle)
  yeniden okunur, token karşılaştırılır ve yazım AYNI transaction'da yapılır.
  Eşleşmezse hiçbir şey yazılmaz.
- Janitor (stuck_janitor.py) failed'a çevirirken token'ı döndürür; geç biten
  eski koşu böylece no-op olur.

Sözleşme: `attempt_id=None` ile çağrılan task (doğrudan çağrı) yalnız token'ı
NULL olan satıra yazabilir. Üretimde dispatch her zaman token üretir.
"""
from __future__ import annotations

import uuid
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.database.models import BrandProfile


def new_attempt_id() -> str:
    return str(uuid.uuid4())


def lock_workspace_row(db: Session, workspace_id: int) -> Optional[BrandProfile]:
    """Satırı FOR UPDATE ile ve TAZE değerlerle okur.

    `populate_existing` şart: oturumun identity map'inde satır zaten yüklüyse
    ORM, kilitli SELECT'in döndürdüğü yeni değerleri YOK SAYIP bayat
    attribute'ları verir; token/status karşılaştırması anlamsız olur.
    """
    return (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .populate_existing()
        .with_for_update()
        .first()
    )


def start_attempt(workspace: BrandProfile) -> str:
    """Yeni attempt token'ı üretir ve (KİLİTLİ) satıra yazar. Commit çağıranın.

    Çağıran satırı `lock_workspace_row` ile kilitlemiş olmalı; token, status
    değişikliğiyle AYNI transaction'da commit edilmelidir.
    """
    token = new_attempt_id()
    workspace.analysis_attempt_id = token
    return token


def lock_if_current(
    db: Session,
    workspace_id: int,
    attempt_id: Optional[str],
    *,
    action: str,
) -> Optional[BrandProfile]:
    """Satırı kilitler; token hâlâ bu attempt'inse satırı, değilse None döner.

    None dönerse kilit bırakılmıştır (rollback) ve çağıran HİÇBİR ŞEY
    yazmadan çıkmalıdır. Eşleşirse satır kilitli kalır; çağıran yazar ve
    commit eder.
    """
    row = lock_workspace_row(db, workspace_id)
    if row is None:
        db.rollback()
        return None
    if row.analysis_attempt_id != attempt_id:
        db.rollback()
        logger.info(
            f"Profil analizi attempt'i eskimiş, '{action}' yazımı atlandı: "
            f"workspace_id={workspace_id} attempt={attempt_id}"
        )
        return None
    return row


def mark_failed_if_current(
    db: Session,
    workspace_id: int,
    attempt_id: Optional[str],
    message: str,
) -> bool:
    """Token eşleşiyorsa status='failed' + error_message yazar (kendi commit'i).

    Eşleşmezse (yeni koşu başladı / janitor token'ı döndürdü) hiçbir şey
    yazmaz ve False döner.
    """
    row = lock_if_current(db, workspace_id, attempt_id, action="failed")
    if row is None:
        return False
    row.status = "failed"
    row.error_message = message
    db.commit()
    return True
