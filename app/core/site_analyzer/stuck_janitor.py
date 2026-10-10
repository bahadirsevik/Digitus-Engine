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

Janitör yazımları KOŞULLUDUR (plan_yapilacaklar.md 2.2, rev. 3): hem toplu hem
tekil yol, profili okuduktan sonra koşulsuz `failed` yazmaz. Yazım anında
tek bir `UPDATE ... WHERE` ile durum (running/pending), yaş ve attempt token'ı
(`analysis_attempt_id IS NOT DISTINCT FROM <okunan>`) yeniden doğrulanır;
Postgres, kilit bekleyen UPDATE'in WHERE'ini son commit'lenmiş satıra karşı
tekrar değerlendirir. Janitörün incelediği eski koşu arada yeniden başlatıldıysa
(yeni token + taze updated_at) ya da bitmişse satıra dokunulmaz. `failed`'a
çevirirken token da DÖNDÜRÜLÜR; böylece janitörün öldürdüğü koşu geç bitse bile
(analysis_attempt.py) hiçbir şey yazamaz.

Kapsam sınırı: YALNIZ BrandProfile statüleri — Celery task'larına (TaskResult)
DOKUNMAZ; onların kendi yaşam döngüsü vardır.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.core.constants import PROFILE_STALE_MINUTES
from app.core.site_analyzer.analysis_attempt import new_attempt_id
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


def _stale_clause(threshold: datetime):
    """SQL yaş koşulu — `_profile_age_reference` ile AYNI kural.

    updated_at, yoksa created_at eşikten eski. Bu iki tanım ayrışmamalı:
    biri değişirse diğeri de değişmeli.
    """
    return or_(
        BrandProfile.updated_at < threshold,
        and_(BrandProfile.updated_at.is_(None), BrandProfile.created_at < threshold),
    )


def _stuck_candidates(db: Session, threshold: datetime) -> List[Tuple[int, Optional[str]]]:
    """Eşikten eski running/pending profillerin (id, görülen attempt token'ı) listesi.

    Bu yalnızca ADAY listesidir; yazım `_fail_row_if_still_stuck` ile yazım
    anında yeniden doğrulanır.
    """
    rows = (
        db.query(BrandProfile.id, BrandProfile.analysis_attempt_id)
        .filter(BrandProfile.status.in_(STUCK_STATUSES))
        .filter(_stale_clause(threshold))
        .all()
    )
    return [(row[0], row[1]) for row in rows]


def _fail_row_if_still_stuck(
    db: Session,
    profile_id: int,
    token_seen: Optional[str],
    threshold: datetime,
) -> bool:
    """Tek koşullu UPDATE: durum + yaş + attempt token'ı YAZIM ANINDA doğrulanır.

    True: satır failed yapıldı (ve token döndürüldü). False: satır bu arada
    değişti (yeni koşu başladı, bitti, onaylandı…) — hiçbir şey yazılmadı.
    Commit çağıranın.
    """
    result = db.execute(
        update(BrandProfile)
        .where(
            BrandProfile.id == profile_id,
            BrandProfile.status.in_(STUCK_STATUSES),
            _stale_clause(threshold),
            BrandProfile.analysis_attempt_id.is_not_distinct_from(token_seen),
        )
        .values(
            status="failed",
            error_message=STUCK_ERROR_MESSAGE,
            # Token döndürülür: bu koşu geç biterse eşleşmeyen token yüzünden no-op olur.
            analysis_attempt_id=new_attempt_id(),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def fail_stuck_profiles(db: Session, *, stale_minutes: int = PROFILE_STALE_MINUTES) -> int:
    """Eşikten eski running/pending profilleri failed işaretler.

    Yaş, `updated_at` üzerinden ölçülür (codex: created_at eski ama yeni
    running'e alınmış kayıtta yanlış pozitif üretir); updated_at boşsa
    created_at'e düşülür. Failed'a çekilen kayıt sayısını döndürür ve loglar.

    Aday listesi okunur, ama her satırın failed yazımı koşulludur
    (`_fail_row_if_still_stuck`): okuma ile yazma arasında yeniden başlatılan
    ya da biten bir satır etkilenmez.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)

    candidates = _stuck_candidates(db, threshold)

    failed_ids: List[int] = []
    for profile_id, token_seen in candidates:
        if _fail_row_if_still_stuck(db, profile_id, token_seen, threshold):
            failed_ids.append(profile_id)
        # Satır başına commit: kilit süresi kısa kalır, bir satırın hatası
        # önceki yazımları geri almaz.
        db.commit()

    if failed_ids:
        logger.warning(
            f"Profil janitörü: {len(failed_ids)} takılı profil failed işaretlendi "
            f"(eşik {stale_minutes} dk): {failed_ids}"
        )
    else:
        logger.info("Profil janitörü: takılı profil yok")

    return len(failed_ids)


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

    Profile None ise veya statü stuck değilse no-op (False). Yaş/durum ön
    kontrolü bellekteki satıra bakar; ASIL karar yazım anında verilir
    (`_fail_row_if_still_stuck`): bellekteki kopya bayatsa (satır bu arada
    yeniden başlatıldı / bitti) hiçbir şey yazılmaz ve False döner. Yazım
    denendiyse commit atılır; böylece `profile` expire olur ve çağıran taze
    değerleri okur.
    """
    if profile is None or profile.status not in STUCK_STATUSES:
        return False

    reference = _profile_age_reference(profile)
    if reference is None:
        return False

    threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    if reference >= threshold:
        return False

    profile_id = profile.id
    token_seen = profile.analysis_attempt_id
    flipped = _fail_row_if_still_stuck(db, profile_id, token_seen, threshold)
    db.commit()
    if not flipped:
        logger.info(
            f"Profil janitörü (lazy): profil {profile_id} yazım anında artık "
            f"takılı değil (yeniden başlatıldı/bitti), dokunulmadı"
        )
        return False

    logger.warning(
        f"Profil janitörü (lazy): profil {profile_id} okuma anında takılı "
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
