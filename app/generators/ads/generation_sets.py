"""ADS üretim versiyonlaması — yaşam döngüsü + yarış güvenliği (plan Faz E).

Ortak dispatcher: full generation VE grup regeneration AYNI kilitten geçer.
Yarış mekanizması: UUID+unique kısıt yarışı KAPATMAZ (her istek farklı UUID
üretir) — ScoringRun satır kilidi (SELECT ... FOR UPDATE) ile serileştirilir;
pending TaskResult + 'generating' AdGenerationSet AYNI transaction'da yazılır,
version_number YALNIZ kilit altında ayrılır.

Final durumlar TEK transaction'da (E2b): set + AdGroup'lar + TaskResult
birlikte commit — update_task_status ayrı session açtığı için final durum
oradan YAZILMAZ (yarım kalırsa stale temizliği başarılı seti failed'a
düşürürdü). Stale reconciliation (E2d) duruma göredir: generating→failed;
active/draft/archived→set korunur, task 'completed' onarılır; failed→task failed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session, Query

from app.core.constants import ADS_TASK_STALE_SECONDS
from app.database.models import AdGenerationSet, AdGroup, ScoringRun, TaskResult

logger = logging.getLogger(__name__)

# Set durumları
SET_GENERATING = "generating"
SET_ACTIVE = "active"
SET_DRAFT = "draft"
SET_ARCHIVED = "archived"
SET_FAILED = "failed"
SUCCESSFUL_STATUSES = (SET_ACTIVE, SET_DRAFT, SET_ARCHIVED)


class AdsGenerationConflict(Exception):
    """Aynı run'da aktif ADS üretimi var — endpoint 409 döner."""

    def __init__(self, message: str, task_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.task_id = task_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_stale_time(started_at, created_at) -> bool:
    ref = started_at or created_at
    if ref is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (_utcnow() - ref) > timedelta(seconds=ADS_TASK_STALE_SECONDS)


def reconcile_stale_ads(db: Session, scoring_run_id: int) -> None:
    """E2d: süresi aşmış ADS task/set kayıtlarını duruma göre uzlaştırır.

    KÖR 'ikisini de failed yap' DEĞİLDİR:
    - Set generating -> task + set failed (üretim yarım kaldı)
    - Set active/draft/archived -> SETE DOKUNMA; task'ı set özetinden
      'completed' olarak ONAR (başarı commit'lenmiş, task güncellemesi yarım)
    - Set failed -> task failed
    Orphan-generating (TaskResult'sız) setler de created_at üzerinden
    aynı eşikle failed edilir (aksi halde run kalıcı 409'a kilitlenir).
    Commit ETMEZ — çağıran (dispatch kilidi altında) commit eder.
    """
    stale_tasks = (
        db.query(TaskResult)
        .filter(
            TaskResult.scoring_run_id == scoring_run_id,
            TaskResult.task_type == "ads",
            TaskResult.status.in_(("pending", "running")),
        )
        .all()
    )
    for task in stale_tasks:
        if not _is_stale_time(task.started_at, task.created_at):
            continue
        linked_set = (
            db.query(AdGenerationSet)
            .filter(AdGenerationSet.task_id == task.task_id)
            .first()
        )
        if linked_set is None or linked_set.status == SET_GENERATING:
            task.status = "failed"
            task.error_message = (
                "Stale: ADS task süre aşımı — üretim yarım kaldı"
            )
            task.completed_at = _utcnow()
            if linked_set is not None:
                linked_set.status = SET_FAILED
                linked_set.completed_at = _utcnow()
            logger.warning(
                "Stale ADS task failed işaretlendi: run=%s task=%s",
                scoring_run_id, task.task_id,
            )
        elif linked_set.status in SUCCESSFUL_STATUSES:
            # Başarı commit'lenmiş — seti KORU, task'ı onar
            task.status = "completed"
            task.progress = 100
            task.completed_at = _utcnow()
            task.result_data = {
                **(task.result_data or {}),
                "reconciled": True,
                "generation_set_id": linked_set.id,
                "version_number": linked_set.version_number,
                "status": linked_set.status,
                "ad_group_count": linked_set.groups_count,
                "failed_groups": linked_set.failed_groups,
            }
            logger.warning(
                "Stale ADS task set özetinden COMPLETED onarıldı: run=%s task=%s set=%s",
                scoring_run_id, task.task_id, linked_set.id,
            )
        else:  # SET_FAILED
            task.status = "failed"
            task.completed_at = _utcnow()

    # Orphan generating setler (TaskResult'ı hiç oluşmamış/silinmiş)
    orphan_sets = (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == scoring_run_id,
            AdGenerationSet.status == SET_GENERATING,
        )
        .all()
    )
    task_ids = {t.task_id for t in stale_tasks}
    active_task_ids = {
        t.task_id
        for t in db.query(TaskResult).filter(
            TaskResult.scoring_run_id == scoring_run_id,
            TaskResult.task_type == "ads",
            TaskResult.status.in_(("pending", "running")),
        )
    }
    for gen_set in orphan_sets:
        has_live_task = gen_set.task_id in active_task_ids and gen_set.task_id not in {
            t.task_id for t in stale_tasks if t.status == "failed"
        }
        if has_live_task:
            continue
        if _is_stale_time(None, gen_set.created_at):
            gen_set.status = SET_FAILED
            gen_set.completed_at = _utcnow()
            logger.warning(
                "Orphan generating set failed işaretlendi: run=%s set=%s",
                scoring_run_id, gen_set.id,
            )


def has_successful_set(db: Session, scoring_run_id: int) -> bool:
    return (
        db.query(AdGenerationSet.id)
        .filter(
            AdGenerationSet.scoring_run_id == scoring_run_id,
            AdGenerationSet.status.in_(SUCCESSFUL_STATUSES),
        )
        .first()
        is not None
    )


def dispatch_ads_generation(
    db: Session,
    scoring_run_id: int,
    task_id: str,
    request_snapshot: dict[str, Any],
) -> AdGenerationSet:
    """Kilit altında guard + kayıt oluşturma (E3 adım 1-3).

    Çağıran task_id'yi (uuid4) üretir ve dönen set commit'lendikten SONRA
    apply_async(task_id=task_id, kwargs={'generation_set_id': set.id, ...})
    çağırır; enqueue hatasında mark_dispatch_failed kullanır.

    Raises:
        AdsGenerationConflict: aktif ads task veya generating set varken.
    """
    # 1. ScoringRun satır kilidi — aynı run'a ikinci istek burada bekler
    run = (
        db.query(ScoringRun)
        .filter(ScoringRun.id == scoring_run_id)
        .with_for_update()
        .first()
    )
    if run is None:
        raise ValueError(f"Scoring run {scoring_run_id} bulunamadı")

    # 2a. Stale reconciliation (kilit altında)
    reconcile_stale_ads(db, scoring_run_id)

    # 2b. Çift guard: aktif ads TaskResult + generating set
    active_task = (
        db.query(TaskResult)
        .filter(
            TaskResult.scoring_run_id == scoring_run_id,
            TaskResult.task_type == "ads",
            TaskResult.status.in_(("pending", "running")),
        )
        .first()
    )
    if active_task is not None:
        raise AdsGenerationConflict(
            "ADS reklam üretimi zaten çalışıyor.", task_id=active_task.task_id
        )
    generating_set = (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == scoring_run_id,
            AdGenerationSet.status == SET_GENERATING,
        )
        .first()
    )
    if generating_set is not None:
        raise AdsGenerationConflict(
            "ADS üretim seti hazırlanıyor.", task_id=generating_set.task_id
        )

    # 3. version_number YALNIZ kilit altında ayrılır; pending TaskResult +
    #    generating set AYNI transaction'da (create_task_record KULLANILMAZ —
    #    ayrı session açar, yarışı yeniden açardı)
    next_version = (
        db.query(AdGenerationSet.version_number)
        .filter(AdGenerationSet.scoring_run_id == scoring_run_id)
        .order_by(AdGenerationSet.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1

    db.add(TaskResult(
        task_id=task_id,
        task_type="ads",
        scoring_run_id=scoring_run_id,
        status="pending",
        progress=0,
        result_data={"operation": request_snapshot.get("operation", "full")},
        created_at=datetime.utcnow(),
    ))
    gen_set = AdGenerationSet(
        scoring_run_id=scoring_run_id,
        task_id=task_id,
        version_number=next_version,
        status=SET_GENERATING,
        is_stale=False,
        request_snapshot=request_snapshot,
    )
    db.add(gen_set)
    db.commit()  # kilit çözülür; ikinci istek generating'i görür -> 409
    db.refresh(gen_set)
    return gen_set


def mark_dispatch_failed(db: Session, task_id: str, error: str) -> None:
    """Broker enqueue hatası: hem task hem set failed (E3 adım 4)."""
    task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
    if task is not None:
        task.status = "failed"
        task.error_message = f"Enqueue hatası: {error[:200]}"
        task.completed_at = _utcnow()
    gen_set = (
        db.query(AdGenerationSet)
        .filter(AdGenerationSet.task_id == task_id)
        .first()
    )
    if gen_set is not None:
        gen_set.status = SET_FAILED
        gen_set.completed_at = _utcnow()
    db.commit()


def finalize_ads_success(
    db: Session,
    gen_set: AdGenerationSet,
    *,
    groups_count: int,
    failed_groups: int,
    warnings: Optional[list] = None,
    extra_result_data: Optional[dict] = None,
) -> str:
    """Başarı finali — set + TaskResult TEK transaction'da (E2b).

    Çağıran AdGroup'ları AYNI session'a eklemiş (commit etmemiş) olmalı.
    Diriltme koruması: set kilitlenir ve hâlâ 'generating' doğrulanır —
    değilse (stale-failed edilmiş) HİÇBİR ŞEY yazılmaz.

    Returns: yeni set status ('active' | 'draft') veya 'aborted'.
    """
    # populate_existing (codex): worker session'ı seti daha önce yüklediyse
    # identity map cache'lenmiş is_stale/status döndürür — kilit altında
    # satır DB'den ZORLA tazelenir, aksi halde mid-generation staleness
    # yarışında eski is_stale=False görülür
    locked = (
        db.query(AdGenerationSet)
        .filter(AdGenerationSet.id == gen_set.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if locked is None or locked.status != SET_GENERATING:
        logger.warning(
            "ADS finalize iptal: set %s durumu '%s' (generating değil) — "
            "sonuç YAZILMADI",
            gen_set.id, locked.status if locked else "yok",
        )
        db.rollback()
        return "aborted"

    # Stale-sonrası semantik: run'da daha önce HİÇ başarılı set yoksa
    # otomatik active; herhangi bir set (legacy/stale dahil) varsa draft.
    prior_success = (
        db.query(AdGenerationSet.id)
        .filter(
            AdGenerationSet.scoring_run_id == locked.scoring_run_id,
            AdGenerationSet.status.in_(SUCCESSFUL_STATUSES),
            AdGenerationSet.id != locked.id,
        )
        .first()
        is not None
    )
    new_status = SET_DRAFT if prior_success else SET_ACTIVE

    # Staleness yarışı (codex bulgusu): üretim SÜRERKEN kanal ataması
    # yenilendiyse set is_stale=True işaretlenmiştir. is_stale ASLA burada
    # sıfırlanmaz (set dispatch'te zaten False doğar) ve bayat set otomatik
    # active OLAMAZ — draft olarak kaydedilir; tüketiciler (active+non-stale)
    # görmez, aktivasyon stale guard'ına takılır.
    if locked.is_stale:
        logger.warning(
            "ADS set %s üretim sırasında bayatladı (kanal ataması yenilendi) "
            "— draft + is_stale=True olarak kaydediliyor, aktive edilemez",
            locked.id,
        )
        new_status = SET_DRAFT

    locked.status = new_status
    locked.groups_count = groups_count
    locked.failed_groups = failed_groups
    locked.warnings = warnings or []
    locked.completed_at = _utcnow()

    task = (
        db.query(TaskResult)
        .filter(TaskResult.task_id == locked.task_id)
        .first()
    )
    if task is not None:
        task.status = "completed"
        task.progress = 100
        task.completed_at = _utcnow()
        task.result_data = {
            **(extra_result_data or {}),
            "generation_set_id": locked.id,
            "version_number": locked.version_number,
            "status": new_status,
            "ad_group_count": groups_count,
            "failed_groups": failed_groups,
        }

    db.commit()  # AdGroup'lar + set + task TEK commit
    return new_status


def finalize_ads_failure(
    db: Session,
    gen_set: AdGenerationSet,
    *,
    error_message: str,
    extra_result_data: Optional[dict] = None,
) -> None:
    """Hata finali — set + TaskResult TEK transaction'da.

    Set zaten finalize edilmişse (completed geç hata) DOKUNULMAZ —
    completed set failed'a DÜŞÜRÜLMEZ (E2b son kural).
    """
    db.rollback()  # yarım kalmış AdGroup insert'leri atılır
    locked = (
        db.query(AdGenerationSet)
        .filter(AdGenerationSet.id == gen_set.id)
        .populate_existing()  # cache'lenmiş status/is_stale tazelenir
        .with_for_update()
        .first()
    )
    if locked is None:
        return
    if locked.status != SET_GENERATING:
        logger.warning(
            "ADS failure finali atlandı: set %s durumu '%s' — completed set "
            "geç hatayla failed'a düşürülmez", gen_set.id, locked.status,
        )
        db.rollback()
        return

    locked.status = SET_FAILED
    locked.completed_at = _utcnow()
    task = (
        db.query(TaskResult)
        .filter(TaskResult.task_id == locked.task_id)
        .first()
    )
    if task is not None:
        task.status = "failed"
        task.error_message = error_message[:500]
        task.completed_at = _utcnow()
        task.result_data = {
            **(extra_result_data or {}),
            "generation_set_id": locked.id,
            "version_number": locked.version_number,
            "status": SET_FAILED,
        }
    db.commit()


def get_active_set(db: Session, scoring_run_id: int) -> Optional[AdGenerationSet]:
    """Aktif + NON-STALE set (export/dashboard/GET varsayılanı)."""
    return (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == scoring_run_id,
            AdGenerationSet.status == SET_ACTIVE,
            AdGenerationSet.is_stale == False,  # noqa: E712
        )
        .first()
    )


def active_ad_groups(db: Session, scoring_run_id: int) -> Query:
    """TÜM ADS tüketicilerinin ortak sorgusu (E2c) —
    filtre AÇIKÇA status='active' AND is_stale=False."""
    return (
        db.query(AdGroup)
        .join(AdGenerationSet, AdGroup.generation_set_id == AdGenerationSet.id)
        .filter(
            AdGroup.scoring_run_id == scoring_run_id,
            AdGenerationSet.status == SET_ACTIVE,
            AdGenerationSet.is_stale == False,  # noqa: E712
        )
    )


def activate_set(db: Session, gen_set: AdGenerationSet) -> AdGenerationSet:
    """Draft/archived seti aktifleştirir (E2 aktivasyon SQL sırası).

    Yalnız grubu BULUNAN, non-stale draft/archived setler; generating ve
    failed aktive EDİLEMEZ. Partial unique index çakışmaması için:
    kilit -> eski aktif archive + FLUSH -> hedef active -> tek commit.
    """
    if gen_set.status not in (SET_DRAFT, SET_ARCHIVED):
        raise ValueError(
            f"Yalnız draft/archived setler aktive edilebilir (durum: {gen_set.status})"
        )
    if gen_set.is_stale:
        raise ValueError(
            "Stale set aktive edilemez — kanal ataması yenilendi, "
            "yeni ADS üretimi gerekli"
        )
    group_count = (
        db.query(AdGroup).filter(AdGroup.generation_set_id == gen_set.id).count()
    )
    if group_count == 0:
        raise ValueError("Boş set aktive edilemez")

    # Run kilidi: eşzamanlı aktivasyon/dispatch serileşir
    db.query(ScoringRun).filter(
        ScoringRun.id == gen_set.scoring_run_id
    ).with_for_update().first()

    current_active = (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == gen_set.scoring_run_id,
            AdGenerationSet.status == SET_ACTIVE,
            AdGenerationSet.id != gen_set.id,
        )
        .first()
    )
    if current_active is not None:
        current_active.status = SET_ARCHIVED
        db.flush()  # partial unique index ihlali olmadan sıra: archive -> active

    gen_set.status = SET_ACTIVE
    db.commit()
    db.refresh(gen_set)
    return gen_set


def mark_run_ad_sets_stale(db: Session, scoring_run_id: int) -> int:
    """Kanal reassignment: run'ın TÜM setleri içerik-stale olur
    (status KORUNUR — task-timeout staleness'tan AYRI kavram).
    Commit ETMEZ (state_machine transaction'ının parçası)."""
    updated = (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == scoring_run_id,
            AdGenerationSet.is_stale == False,  # noqa: E712
        )
        .update({AdGenerationSet.is_stale: True}, synchronize_session=False)
    )
    return updated
