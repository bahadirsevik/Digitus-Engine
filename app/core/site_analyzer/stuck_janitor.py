"""Takili profil janitörü (P7 Adım 6 + lazy read-time takip).

Site profil analizi FastAPI BackgroundTasks ile WEB PROCESS içinde koşar
(app/api/v1/brand_profile.py). App restart olursa `running`/`pending`
statüsündeki BrandProfile kayıtları sonsuza dek takılı kalır — arka planda
onları bitirecek kimse yoktur.

`fail_stuck_profiles` app startup'ında bir kez çalışır: eşikten
(PROFILE_STALE_MINUTES) eski running/pending profilleri `failed` işaretler.
Ama bu TEK seferlik taramadır ve kasıtlı olarak taze satırları atlar — bir
restart TAM eşik anında olmazsa (ör. 10:00 running, 10:02 restart), janitör
o an taze görünen satırı bir daha asla göremez ve kayıt sonsuza dek
running'de takılı kalır.

Bu yüzden `fail_if_stuck`, aynı eşik kuralını OKUMA anında (get_profile /
get_workspace) tek satır üzerinde uygular — bu repo'nun zaten kullandığı
lazy-staleness deseni (bkz. PREVIEW_STALE_MINUTES / DISCOVERY_STALE_MINUTES,
app/api/v1/brand_profile.py). Her okumada çağrılması güvenlidir: değişiklik
yoksa no-op + commit yok.

Kapsam sınırı: YALNIZ BrandProfile statüleri — Celery task'larına (TaskResult)
DOKUNMAZ; onların kendi yaşam döngüsü vardır.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.constants import PROFILE_STALE_MINUTES
from app.database.models import BrandProfile

STUCK_STATUSES = ("running", "pending")
STUCK_ERROR_MESSAGE = "Uygulama yeniden başlatıldı; analizi tekrar deneyin."


def _profile_age_reference(profile: BrandProfile) -> Optional[datetime]:
    """Yaş ölçümü için referans zaman damgası: updated_at, yoksa created_at.

    `fail_stuck_profiles` (toplu tarama) ve `fail_if_stuck` (tekil, okuma
    anında) AYNI kuralı kullanmak zorunda — aksi halde biri stuck derken
    diğeri taze diyebilir. Postgres'ten naive dönebilen değerler UTC-aware'a
    normalize edilir (aware/naive karşılaştırma TypeError fırlatır).
    """
    reference = profile.updated_at if profile.updated_at is not None else profile.created_at
    if reference is None:
        return None
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference


def fail_stuck_profiles(db: Session, *, stale_minutes: int = PROFILE_STALE_MINUTES) -> int:
    """Eşikten eski running/pending profilleri failed işaretler.

    Yaş, `updated_at` üzerinden ölçülür (codex: created_at eski ama yeni
    running'e alınmış kayıtta yanlış pozitif üretir); updated_at boşsa
    created_at'e düşülür. Failed'a çekilen kayıt sayısını döndürür ve loglar.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)

    stuck = (
        db.query(BrandProfile)
        .filter(BrandProfile.status.in_(STUCK_STATUSES))
        .filter(
            or_(
                BrandProfile.updated_at < threshold,
                BrandProfile.updated_at.is_(None) & (BrandProfile.created_at < threshold),
            )
        )
        .all()
    )

    for profile in stuck:
        profile.status = "failed"
        profile.error_message = STUCK_ERROR_MESSAGE

    if stuck:
        db.commit()
        logger.warning(
            f"Profil janitörü: {len(stuck)} takılı profil failed işaretlendi "
            f"(eşik {stale_minutes} dk): {[p.id for p in stuck]}"
        )
    else:
        logger.info("Profil janitörü: takılı profil yok")

    return len(stuck)


def fail_if_stuck(
    db: Session,
    profile: Optional[BrandProfile],
    *,
    stale_minutes: int = PROFILE_STALE_MINUTES,
) -> bool:
    """Tekil profili OKUMA anında (get_profile/get_workspace) kontrol eder.

    Startup janitörü tek seferlik olduğu için, eşik anında henüz taze olan
    (dolayısıyla atlanan) bir satır bir daha asla taranmaz — kalıcı olarak
    running/pending'de takılı kalır. Bu fonksiyon her okumada çağrılarak
    aynı eşik kuralını lazy uygular (repo'daki PREVIEW/DISCOVERY_STALE_MINUTES
    deseniyle aynı yaklaşım).

    Profile None ise veya statü stuck değilse no-op (False). Değişiklik
    yoksa commit ATILMAZ.
    """
    if profile is None or profile.status not in STUCK_STATUSES:
        return False

    reference = _profile_age_reference(profile)
    if reference is None:
        return False

    threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    if reference >= threshold:
        return False

    profile.status = "failed"
    profile.error_message = STUCK_ERROR_MESSAGE
    db.commit()
    logger.warning(
        f"Profil janitörü (lazy): profil {profile.id} okuma anında takılı "
        f"bulundu, failed işaretlendi (eşik {stale_minutes} dk)"
    )
    return True


def run_startup_janitor() -> None:
    """Startup'ta güvenli çağrı — hata app açılışını engellemez."""
    from app.database.connection import SessionLocal

    db = SessionLocal()
    try:
        fail_stuck_profiles(db)
    except Exception as e:  # janitör hatası açılışı durdurmasın
        logger.error(f"Profil janitörü çalıştırılamadı: {e}")
    finally:
        db.close()
